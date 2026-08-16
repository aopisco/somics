#!/bin/bash
# Publish the somics spatial atlas to the public R2 bucket.
#
# The atlas is read straight from R2 by the viewer and by the notebooks —
# src/somics/viewer/atlas_source.py, README.md and the corpus index all point at
# s3://epiblast-public/somics_spatial_atlas — so this is the step that makes a
# newly ingested dataset visible to anyone but this machine.
#
# Credentials come from the environment. s5cmd only reads the AWS-named
# variables, so the R2_* ones are mapped onto them for the duration of the
# command rather than exported: the read-only key pair published in the README
# is a different, deliberately public pair, and these write credentials should
# not leak into the environment of anything else.
#
# `sync` and not `sync --delete`: the atlas is append-mostly, a stale local copy
# would otherwise delete live objects, and the zarr groups of datasets ingested
# from another machine are not reproducible from here. Removals are done
# deliberately, by hand.
#
# Run:
#     scripts/sync_atlas_to_r2.sh [--dry-run] [ATLAS_DIR]
set -euo pipefail

ATLAS_PARENT="/home/ubuntu/polycomb_atlases"
BUCKET="s3://epiblast-public"

DRY_RUN=""
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN="--dry-run"
  shift
fi
ATLAS_NAME="$(basename "${1:-somics_spatial_atlas}")"

for v in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT_URL R2_REGION; do
  if [ -z "${!v:-}" ]; then
    echo "error: $v is not set; the R2 write credentials live in ~/.bashrc" >&2
    exit 1
  fi
done

if [ ! -d "$ATLAS_PARENT/$ATLAS_NAME" ]; then
  echo "error: no atlas at $ATLAS_PARENT/$ATLAS_NAME" >&2
  exit 1
fi

echo "syncing $ATLAS_NAME -> $BUCKET/$ATLAS_NAME/ ${DRY_RUN:+(dry run)}"
cd "$ATLAS_PARENT"
AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
AWS_REGION="$R2_REGION" \
  s5cmd $DRY_RUN --endpoint-url "$R2_ENDPOINT_URL" --stat \
    sync "$ATLAS_NAME/" "$BUCKET/$ATLAS_NAME/"
