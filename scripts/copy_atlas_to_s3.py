"""Copy the somics spatial atlas from Cloudflare R2 to an S3 bucket.

The atlas is authored on the ingest machine and published to R2, which is the
only copy anything reads. This mirrors it into a bucket you own, so the atlas
survives losing access to that R2 bucket.

The copy streams object-by-object through this machine because R2 and S3 are
different providers — there is no server-side copy between them. It is
restartable: an object already present at the destination with the same size is
skipped, so re-running after an interruption resumes rather than restarts.

Reads use the public read-only R2 credentials published in the project README.
Writes use a named AWS profile, so no AWS secret ever appears in this file.

Run:
    uv run --with boto3 python scripts/copy_atlas_to_s3.py \
        --dest-bucket somics-dev --profile sci-data-dev-poweruser
    ... --dry-run     # list what would be copied and stop
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import threading

import boto3
from botocore.config import Config

R2_ENDPOINT = "https://61be05560bebc4714cdd9913fb075bc9.r2.cloudflarestorage.com"
R2_KEY = "087ee61ad71e3fc431f7c8031545c4e4"
R2_SECRET = "3c94e43945c4e49a466930527f368756810315f68ad26a2c10c8adac2ed08b8d"
R2_BUCKET = "epiblast-public"
PREFIX = "somics_spatial_atlas/"

_print_lock = threading.Lock()


def r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_KEY,
        aws_secret_access_key=R2_SECRET,
        region_name="auto",
        config=Config(signature_version="s3v4", max_pool_connections=32),
    )


def list_objects(client, bucket, prefix):
    """key -> size for everything under prefix."""
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            out[o["Key"]] = o["Size"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest-bucket", required=True)
    ap.add_argument(
        "--profile",
        help="AWS profile; omit on EC2 to use the instance role via the default credential chain",
    )
    ap.add_argument("--prefix", default=PREFIX, help="Source prefix; also the destination prefix")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = r2_client()
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    dst = session.client("s3", config=Config(max_pool_connections=args.workers * 2))

    print(f"scanning r2://{R2_BUCKET}/{args.prefix} ...", flush=True)
    source = list_objects(src, R2_BUCKET, args.prefix)
    print(f"  {len(source):,} objects, {sum(source.values()) / 1e9:.2f} GB")

    print(f"scanning s3://{args.dest_bucket}/{args.prefix} ...", flush=True)
    dest = list_objects(dst, args.dest_bucket, args.prefix)
    print(f"  {len(dest):,} objects already there")

    todo = [k for k, size in source.items() if dest.get(k) != size]
    total = sum(source[k] for k in todo)
    print(f"to copy: {len(todo):,} objects, {total / 1e9:.2f} GB")
    if args.dry_run:
        for k in todo[:20]:
            print("  would copy", k)
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20:,} more")
        return 0
    if not todo:
        print("destination is already in sync.")
        return 0

    done = {"n": 0, "bytes": 0}

    def copy_one(key):
        body = src.get_object(Bucket=R2_BUCKET, Key=key)["Body"]
        dst.upload_fileobj(body, args.dest_bucket, key)
        with _print_lock:
            done["n"] += 1
            done["bytes"] += source[key]
            if done["n"] % 50 == 0 or done["n"] == len(todo):
                pct = 100 * done["bytes"] / total if total else 100
                print(
                    f"  {done['n']:,}/{len(todo):,} objects · "
                    f"{done['bytes'] / 1e9:.2f}/{total / 1e9:.2f} GB ({pct:.0f}%)",
                    flush=True,
                )

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(copy_one, k): k for k in todo}
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - report and continue; re-run resumes
                failures.append((futures[f], repr(e)[:120]))

    print(f"\ncopied {done['n']:,} objects ({done['bytes'] / 1e9:.2f} GB)")
    if failures:
        print(f"{len(failures)} FAILED (re-run to retry just these):")
        for k, err in failures[:10]:
            print(" ", k, "-", err)
        return 1

    after = list_objects(dst, args.dest_bucket, args.prefix)
    missing = [k for k, size in source.items() if after.get(k) != size]
    if missing:
        print(f"VERIFY FAILED: {len(missing):,} objects missing or size-mismatched")
        return 1
    print(f"verified: {len(after):,} objects, {sum(after.values()) / 1e9:.2f} GB — matches source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
