#!/bin/bash
# Build the viewer index and the corpus index against an atlas prefix, from EC2
# (the obs scans are minutes and gigabytes; not laptop work). Uploads to
#   s3://somics-dev/viewer_cache/<atlas stamp>/{samples.json,coords/,corpus_index.json}
# Run as user-data:
#   export SOMICS_ATLAS_DIR=s3://somics-dev/ingest/<family>/atlas/<stamp>
#   export SOMICS_BRANCH=protein-adapters
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/build_viewer_cache_ec2.sh | bash
set -uo pipefail
exec > /var/log/viewer_cache.log 2>&1
ATLAS=${SOMICS_ATLAS_DIR:?set SOMICS_ATLAS_DIR}
BRANCH=${SOMICS_BRANCH:-main}
STAMP=$(basename "$ATLAS")
DEST=s3://somics-dev/viewer_cache/$STAMP
REGION=us-east-1; export AWS_REGION=$REGION
finish() { aws s3 cp /var/log/viewer_cache.log $DEST/_build.log --region $REGION; shutdown -h now; }
dnf install -y git; export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="/root/.local/bin:$PATH"
D=/mnt/work; mkdir -p $D && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || finish
cd repo && uv sync || finish
export SOMICS_ATLAS_STORE=aws
uv run python scripts/build_viewer_cache.py --out $D/viewer_cache || { echo "VIEWER CACHE FAILED"; finish; }
uv run python scripts/build_corpus_index.py -o $D/viewer_cache/corpus_index.json || echo "CORPUS INDEX FAILED (viewer cache still uploaded)"
aws s3 sync $D/viewer_cache $DEST --region $REGION --only-show-errors || finish
echo "DONE $STAMP" | aws s3 cp - $DEST/_DONE --region $REGION
finish
