#!/bin/bash
# Build the corpus builder's precomputed dataset pages for every card, on EC2,
# in N concurrent shards (each shard is one build_dataset_pages.py process with
# --only for its cards; all write into one output dir, and each process's
# final write_manifest lists whatever pages exist, so the last one is complete).
# Uploads to s3://somics-dev/viewer_cache/<atlas stamp>/dataset_pages/.
#
#   export SOMICS_ATLAS_DIR=s3://somics-dev/ingest/<family>/atlas/<stamp>
#   export SOMICS_BRANCH=protein-adapters  SOMICS_SHARDS=3
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/build_dataset_pages_ec2.sh | bash
set -uo pipefail
exec > /var/log/dataset_pages.log 2>&1
ATLAS=${SOMICS_ATLAS_DIR:?set SOMICS_ATLAS_DIR}; BRANCH=${SOMICS_BRANCH:-main}; SHARDS=${SOMICS_SHARDS:-3}
STAMP=$(basename "$ATLAS"); DEST=s3://somics-dev/viewer_cache/$STAMP/dataset_pages
REGION=us-east-1; export AWS_REGION=$REGION SOMICS_ATLAS_STORE=aws
finish() { aws s3 cp /var/log/dataset_pages.log $DEST/_build.log --region $REGION; shutdown -h now; }
dnf install -y git; export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="/root/.local/bin:$PATH"
D=/mnt/work; mkdir -p $D && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || finish
cd repo && uv sync || finish
aws s3 cp s3://somics-dev/viewer_cache/$STAMP/corpus_index.json data/corpus_index.json --region $REGION || finish
OUT=$D/dataset_pages; mkdir -p $OUT
uv run python - "$SHARDS" <<'PY'
import json, sys
ids=[c["id"] for c in json.load(open("data/corpus_index.json"))["datasets"]]
n=int(sys.argv[1])
for i in range(n):
    open(f"/mnt/work/shard_{i}.txt","w").write("\n".join(ids[i::n]))
print(len(ids), "cards in", n, "shards")
PY
for i in $(seq 0 $((SHARDS-1))); do
  ONLY=$(sed 's/^/--only /' /mnt/work/shard_$i.txt | tr '\n' ' ')
  ( uv run python scripts/build_dataset_pages.py --atlas "$ATLAS" -o $OUT $ONLY > $D/shard_$i.log 2>&1; echo "shard $i exit $?" ) &
done
wait
# one final manifest over everything that landed (and re-render nothing)
uv run python scripts/build_dataset_pages.py --atlas "$ATLAS" -o $OUT --html-only || true
grep -c '"slug"' $OUT/manifest.json; du -sh $OUT
aws s3 sync $OUT $DEST --region $REGION --only-show-errors || finish
for i in $(seq 0 $((SHARDS-1))); do aws s3 cp $D/shard_$i.log $DEST/_shard_$i.log --region $REGION; done
echo "DONE $STAMP $(ls $OUT | wc -l) entries" | aws s3 cp - $DEST/_DONE --region $REGION
finish
