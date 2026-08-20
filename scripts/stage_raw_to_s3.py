"""Mirror raw dataset bundles from their public sources into S3.

The registry records where each dataset lives on the internet; this fetches
those bundles into `s3://<bucket>/raw/<dataset_id>/` so the corpus survives link
rot and so ingestion can read from S3 instead of re-downloading. It needs none
of the ingest pipeline — it is plain HTTP to S3.

Design notes, all learned from the sources themselves:

* **Streamed, never staged on local disk.** Bundles run to hundreds of GB;
  `upload_fileobj` pushes the HTTP response straight into a multipart upload.
* **Resumable.** A dataset whose `_manifest.json` records a completed fetch of
  the same source URL and byte count is skipped, so a re-run after an
  interruption resumes rather than restarts.
* **A manifest per dataset**, written last, holding the source URL, byte count,
  md5, HTTP status and fetch time. Written last so a half-finished object is
  never mistaken for a complete one.
* **Hosts differ.** Some 404, some are behind Cloudflare and reject non-browser
  agents, some redirect several times. Failures are collected and reported
  rather than aborting the run.

Run:
    uv run --with boto3 python scripts/stage_raw_to_s3.py \
        --bucket somics-dev --profile sci-data-dev-poweruser --limit 10
    ... --max-bytes 20e9     # skip bundles larger than this
    ... --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

REPO = Path(__file__).resolve().parents[1]
# Hosts disagree about what they want. Cloudflare-fronted sites (10x) reject a
# plain agent; Dropbox does the opposite, serving an HTML preview to anything
# that looks like a browser. Try a browser agent first, then a bare one.
USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Mozilla/5.0",
)
TIMEOUT = 60


def direct_url(url):
    """Rewrite hosts that serve an HTML preview unless told otherwise."""
    if "dropbox.com" in url:
        base = re.sub(r"[?&]dl=[01]", "", url)
        return base + ("&dl=1" if "?" in base else "?dl=1")
    if "drive.google.com" in url and "/file/d/" in url:
        m = re.search(r"/file/d/([^/]+)", url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


class HtmlResponse(Exception):
    """The host returned a web page where a data file was expected."""


class Counting:
    """File-like wrapper that checksums and counts bytes as boto3 reads them.

    It also inspects the first chunk: several hosts answer 200 with an HTML
    landing or preview page instead of the file, which would otherwise be
    stored and recorded as a successful fetch.
    """

    def __init__(self, fh, expect_html=False):
        self.fh, self.md5, self.n = fh, hashlib.md5(), 0
        self.expect_html, self.checked = expect_html, False

    def read(self, size=-1):
        chunk = self.fh.read(size)
        if chunk and not self.checked:
            self.checked = True
            head = chunk[:512].lstrip().lower()
            is_html = head.startswith(b"<!doctype html") or head.startswith(b"<html")
            if is_html and not self.expect_html:
                raise HtmlResponse("host returned an HTML page, not a data file")
        if chunk:
            self.md5.update(chunk)
            self.n += len(chunk)
        return chunk


def filename_for(url, dataset_id):
    path = re.split(r"[?#]", url)[0].rstrip("/")
    segs = [urllib.parse.unquote(s) for s in path.split("/") if s]
    # Zenodo serves .../files/<real name>/content, so the last segment is a
    # constant and the name is the one before it.
    if segs and segs[-1] in ("content", "download", "file"):
        segs = segs[:-1]
    tail = segs[-1] if segs else ""
    if tail and "." in tail and len(tail) < 120:
        return tail
    if "acc=" in url:  # GEO bulk download
        acc = re.search(r"acc=([A-Za-z0-9]+)", url)
        if acc:
            return f"{acc.group(1)}_RAW.tar"
    return f"{dataset_id}.download"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument(
        "--profile",
        help="AWS profile; omit on EC2 to use the instance role via the default credential chain",
    )
    ap.add_argument("--prefix", default="raw")
    ap.add_argument("--limit", type=int, help="only the first N datasets")
    ap.add_argument(
        "--diverse",
        action="store_true",
        help="pick one dataset from each distinct host, so a trial run "
        "exercises many hosts rather than many bundles on one",
    )
    ap.add_argument("--max-bytes", type=float, default=None, help="skip bundles above this size")
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="datasets fetched concurrently. Each source is individually slow "
        "(~10 MB/s observed), so wall time scales down almost linearly "
        "with this until the local NIC saturates.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client(
        "s3",
        config=Config(max_pool_connections=max(16, args.workers * 8), retries={"max_attempts": 3}),
    )
    xfer = TransferConfig(
        multipart_threshold=64 * 1024**2, multipart_chunksize=64 * 1024**2, max_concurrency=4
    )

    rows = [r for r in csv.DictReader(open(REPO / "data" / "datasets.csv")) if r["download_url"]]
    if args.diverse:
        by_host = {}
        for r in rows:
            host = re.sub(r"^https?://", "", r["download_url"]).split("/")[0].lower()
            by_host.setdefault(host, []).append(r)
        rows = [v[0] for v in by_host.values()]
        print(f"--diverse: one dataset from each of {len(rows)} hosts")
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows)} datasets with a download_url, {args.workers} worker(s)\n", flush=True)

    lock = threading.Lock()
    n = {"done": 0, "skipped": 0, "failed": 0, "bytes": 0}
    failures = []

    def tally(key, **extra):
        with lock:
            n[key] += 1
            if "nbytes" in extra:
                n["bytes"] += extra["nbytes"]
            if "failure" in extra:
                failures.append(extra["failure"])

    def handle(item):
        i, r = item
        did, url = r["dataset_id"], r["download_url"].strip()
        key_prefix = f"{args.prefix}/{did}"
        mkey = f"{key_prefix}/_manifest.json"

        try:  # already staged?
            prev = json.loads(s3.get_object(Bucket=args.bucket, Key=mkey)["Body"].read())
            if prev.get("source_url") == url and prev.get("bytes", 0) > 0:
                print(f"[{i}/{len(rows)}] skip (already staged) {did}", flush=True)
                tally("skipped")
                return
        except Exception:
            pass

        if args.dry_run:
            print(f"[{i}/{len(rows)}] would fetch {did} <- {url[:80]}", flush=True)
            return

        fetch_url = direct_url(url)
        name = filename_for(fetch_url, did)
        okey = f"{key_prefix}/{name}"
        t0 = time.time()
        wrapped = status = None
        last_err = "unknown"

        for attempt, ua in enumerate(USER_AGENTS):
            try:
                req = urllib.request.Request(fetch_url, headers={"User-Agent": ua})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    declared = int(resp.headers.get("Content-Length") or 0)
                    if args.max_bytes and declared > args.max_bytes:
                        print(
                            f"[{i}/{len(rows)}] skip (>{args.max_bytes / 1e9:.0f}GB: "
                            f"{declared / 1e9:.1f}GB) {did}",
                            flush=True,
                        )
                        tally("skipped")
                        return
                    size_note = f"({declared / 1e6:.0f} MB)" if declared else "(size unknown)"
                    retry_note = " [retry, plain agent]" if attempt else ""
                    print(f"[{i}/{len(rows)}] {did} <- {name} {size_note}{retry_note}", flush=True)
                    w = Counting(resp, expect_html=name.lower().endswith((".html", ".htm")))
                    s3.upload_fileobj(w, args.bucket, okey, Config=xfer)
                    wrapped, status = w, resp.status
                    break
            except Exception as e:
                cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
                if isinstance(e, HtmlResponse) or isinstance(cause, HtmlResponse):
                    last_err = "html page, not data"
                    s3.delete_object(Bucket=args.bucket, Key=okey)
                    continue  # try the next user agent
                if isinstance(e, urllib.error.HTTPError):
                    last_err = f"http {e.code}"
                else:
                    last_err = type(e).__name__
                break

        if wrapped is None:
            print(f"    FAILED {did}: {last_err}", flush=True)
            tally("failed", failure=(did, url, last_err))
            return
        if wrapped.n == 0:
            print(f"    FAILED {did}: empty body", flush=True)
            s3.delete_object(Bucket=args.bucket, Key=okey)
            tally("failed", failure=(did, url, "empty body"))
            return

        s3.put_object(
            Bucket=args.bucket,
            Key=mkey,
            ContentType="application/json",
            Body=json.dumps(
                {
                    "dataset_id": did,
                    "dataset_name": r["dataset_name"],
                    "source_url": url,
                    "s3_key": okey,
                    "bytes": wrapped.n,
                    "md5": wrapped.md5.hexdigest(),
                    "http_status": status,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "original_publication_link": r["original_publication_link"],
                },
                indent=1,
            ).encode(),
        )
        dt = time.time() - t0
        tally("done", nbytes=wrapped.n)
        print(
            f"    ok {did}: {wrapped.n / 1e6:.0f} MB in {dt:.0f}s "
            f"({wrapped.n / 1e6 / max(dt, 1):.1f} MB/s)",
            flush=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(handle, enumerate(rows, 1)))

    print(
        f"\nstaged {n['done']} · skipped {n['skipped']} · failed {n['failed']} · "
        f"{n['bytes'] / 1e9:.2f} GB"
    )
    if failures:
        print("\nfailures:")
        for did, url, why in failures:
            print(f"  {why:20} {did[:34]:36} {url[:60]}")
    return 1 if n["failed"] and not n["done"] else 0


if __name__ == "__main__":
    sys.exit(main())
