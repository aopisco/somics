"""Stage HuBMAP spatial datasets into S3, whole datasets rather than single files.

The literature stager fetches one bundle per dataset, because that is what a
Zenodo or GEO deposit usually is. A HuBMAP dataset is a directory — 44 files on
average across the Tier 2 selection — and only makes sense complete, so this
walks each dataset's file list and mirrors all of it.

Access route, which is not the documented one (HuBMAP points at Globus):

  file list   POST search.api.hubmapconsortium.org/v3/files/search
              — a second index; the main portal index carries no file fields
  download    GET  assets.hubmapconsortium.org/<uuid>/<rel_path>
              — plain HTTP, no auth, for datasets without human genetic
                sequence. Those with it are absent from the index entirely and
                need controlled-access authorisation, which no transport fixes.

Layout mirrors the source so a dataset stays reassemblable:

  s3://<bucket>/hubmap/<hubmap_id>/<rel_path>
  s3://<bucket>/hubmap/<hubmap_id>/_manifest.json

Resumable at file granularity: an object already present with the right size is
skipped, so an interrupted run resumes mid-dataset rather than restarting it.

Run:
    uv run --with boto3 python scripts/stage_hubmap_to_s3.py \
        --bucket somics-dev --tier 2 --workers 12
    ... --dry-run          # size the selection and stop
    ... --profile NAME     # omit on EC2 to use the instance role
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

SEARCH = "https://search.api.hubmapconsortium.org/v3/portal/search"
FILES = "https://search.api.hubmapconsortium.org/v3/files/search"
ASSETS = "https://assets.hubmapconsortium.org"
UA = {"User-Agent": "Mozilla/5.0 (compatible; somics-hubmap-stager)"}

# Assay tiers, chosen by volume per dataset. Raw CODEX and PhenoCycler are
# excluded from tiers 1-2 deliberately: together they are 87 TB and 9.8M files
# — 75% of the corpus volume for 12% of its datasets — and the SPRM-processed
# variants of much of the same tissue are already in tier 1.
TIER1 = [
    "MIBI",
    "MIBI [DeepCell + SPRM]",
    "MALDI",
    "Xenium",
    "seqFISH",
    "seqFISH [Lab Processed]",
    "CODEX [Cytokit + SPRM]",
    "PhenoCycler [DeepCell + SPRM]",
    "Cell DIVE [DeepCell + SPRM]",
    "DESI",
    "SIMS",
    "Visium (no probes)",
]
TIER2 = TIER1 + [
    "Histology",
    "Histology [Kaggle-1 Segmentation]",
    "Histology [Kaggle-1 Glomerulus Segmentation]",
    "Auto-fluorescence",
]
TIERS = {1: TIER1, 2: TIER2}

_lock = threading.Lock()


def post(url, payload, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception:
            time.sleep(2)
    return None


def datasets_for(assays):
    """Every published dataset of these assay types that has indexed files."""
    out, after = [], None
    while True:
        q = {
            "size": 500,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"entity_type.keyword": "Dataset"}},
                        {"term": {"status.keyword": "Published"}},
                        {"terms": {"dataset_type.keyword": assays}},
                    ]
                }
            },
            "sort": [{"uuid.keyword": "asc"}],
            "_source": [
                "uuid",
                "hubmap_id",
                "dataset_type",
                "anatomy_1",
                "contains_human_genetic_sequences",
            ],
        }
        if after:
            q["search_after"] = after
        d = post(SEARCH, q)
        hits = (d or {}).get("hits", {}).get("hits", [])
        if not hits:
            break
        out += [h["_source"] for h in hits]
        after = hits[-1]["sort"]
    return out


def files_for(uuid):
    out, after = [], None
    while True:
        q = {
            "size": 1000,
            "query": {"term": {"dataset_uuid.keyword": uuid}},
            "sort": [{"rel_path.keyword": "asc"}],
            "_source": ["rel_path", "size"],
        }
        if after:
            q["search_after"] = after
        d = post(FILES, q)
        hits = (d or {}).get("hits", {}).get("hits", [])
        if not hits:
            break
        out += [(h["_source"]["rel_path"], h["_source"].get("size") or 0) for h in hits]
        after = hits[-1]["sort"]
    return out


class Counting:
    def __init__(self, fh):
        self.fh, self.md5, self.n = fh, hashlib.md5(), 0

    def read(self, size=-1):
        chunk = self.fh.read(size)
        if chunk:
            self.md5.update(chunk)
            self.n += len(chunk)
        return chunk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--profile")
    ap.add_argument("--prefix", default="hubmap")
    ap.add_argument("--tier", type=int, default=2, choices=[1, 2])
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client(
        "s3",
        config=Config(max_pool_connections=max(16, args.workers * 4), retries={"max_attempts": 3}),
    )
    xfer = TransferConfig(
        multipart_threshold=64 * 1024**2, multipart_chunksize=64 * 1024**2, max_concurrency=4
    )

    ds = datasets_for(TIERS[args.tier])
    ds = [d for d in ds if not d.get("contains_human_genetic_sequences")]
    if args.limit:
        ds = ds[: args.limit]
    print(
        f"tier {args.tier}: {len(ds)} published datasets without genetic-sequence gating",
        flush=True,
    )

    n = {"files": 0, "bytes": 0, "skipped": 0, "failed": 0, "done": 0}
    failures = []

    def stage(item):
        i, meta = item
        uid, hid = meta["uuid"], meta.get("hubmap_id") or meta["uuid"]
        base = f"{args.prefix}/{hid}"
        files = files_for(uid)
        if not files:
            with _lock:
                n["skipped"] += 1
            return
        total = sum(sz for _, sz in files)
        if args.dry_run:
            with _lock:
                n["files"] += len(files)
                n["bytes"] += total
                n["done"] += 1
            return
        print(
            f"[{i}/{len(ds)}] {hid} · {meta.get('dataset_type', '?')[:22]} · "
            f"{len(files)} files · {total / 1e9:.2f} GB",
            flush=True,
        )

        got = 0
        for rel, sz in files:
            key = f"{base}/{rel}"
            try:  # already there at the right size?
                if s3.head_object(Bucket=args.bucket, Key=key)["ContentLength"] == sz and sz:
                    continue
            except Exception:
                pass
            url = f"{ASSETS}/{uid}/{urllib.parse.quote(rel)}"
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=180
                ) as resp:
                    w = Counting(resp)
                    s3.upload_fileobj(w, args.bucket, key, Config=xfer)
                got += w.n
            except Exception as e:
                code = getattr(e, "code", type(e).__name__)
                with _lock:
                    failures.append((hid, rel, str(code)))
                continue
        s3.put_object(
            Bucket=args.bucket,
            Key=f"{base}/_manifest.json",
            ContentType="application/json",
            Body=json.dumps(
                {
                    "hubmap_id": hid,
                    "uuid": uid,
                    "dataset_type": meta.get("dataset_type"),
                    "organ": (meta.get("anatomy_1") or [None])[0],
                    "n_files": len(files),
                    "bytes_indexed": total,
                    "bytes_fetched_this_run": got,
                    "source": f"{ASSETS}/{uid}/",
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                indent=1,
            ).encode(),
        )
        with _lock:
            n["done"] += 1
            n["files"] += len(files)
            n["bytes"] += got
            if n["done"] % 25 == 0:
                print(
                    f"  ... {n['done']}/{len(ds)} datasets, {n['bytes'] / 1e12:.2f} TB", flush=True
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(stage, enumerate(ds, 1)))

    if args.dry_run:
        print(
            f"\nwould stage {n['done']} datasets · {n['files']:,} files · "
            f"{n['bytes'] / 1e12:.2f} TB"
        )
        return 0
    print(
        f"\nstaged {n['done']} datasets · {n['files']:,} files · {n['bytes'] / 1e12:.2f} TB "
        f"· {n['skipped']} without files · {len(failures)} file errors"
    )
    for hid, rel, why in failures[:15]:
        print(f"  {why:14} {hid} {rel[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
