#!/bin/bash
# Repair a finished atlas prefix's obs table on a large box and write it back
# under a new prefix. See scripts/repair_atlas.py for what and why.
#
# Wrapper user-data:
#   #!/bin/bash
#   export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/<family>/atlas/<stamp>
#   export SOMICS_BRANCH=protein-adapters
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/repair_atlas_ec2.sh | bash
#
# Needs memory for the whole obs table (22.9M rows -> use r5.4xlarge, 128 GB)
# and disk for the atlas (230 GB at the end of the Visium block). No polycomb
# skills or reference cache: only the schema resolution, which `uv sync` covers.
set -x
exec > /var/log/repair.log 2>&1
B=s3://somics-dev/ingest/repair
BASE_ATLAS=${SOMICS_BASE_ATLAS:?set SOMICS_BASE_ATLAS}
BRANCH=${SOMICS_BRANCH:-main}
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
DEST=$B/atlas/$STAMP
REGION=us-east-1
fail() { aws s3 cp /var/log/repair.log $B/repair-FAILED-$STAMP.log --region $REGION; shutdown -h now; exit 1; }

dnf install -y git
export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
D=/mnt/work; mkdir -p $D && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || fail
cd repo && uv sync || fail
cd $D

ATLAS=$D/atlas; mkdir -p $ATLAS
aws s3 sync $BASE_ATLAS/ $ATLAS --exclude "_*" --region $REGION --only-show-errors || fail
[ "$(du -sm $ATLAS | cut -f1)" -gt 20000 ] || fail
echo "base atlas: $BASE_ATLAS" > $D/provenance.txt

cd $D/repo
PYTHONPATH=$D/repo/src uv run python scripts/repair_atlas.py --atlas $ATLAS --schema $D/repo/schema/spatial_omics_atlas_schema.yaml > $D/repair.txt 2>&1; RC=$?
cat $D/repair.txt
[ $RC -eq 0 ] || fail

aws s3 sync $ATLAS $DEST --region $REGION --only-show-errors || fail
aws s3 cp $D/repair.txt $DEST/_repair.txt --region $REGION
aws s3 cp $D/provenance.txt $DEST/_provenance.txt --region $REGION
aws s3 cp /var/log/repair.log $DEST/_repair.log --region $REGION
echo "DONE $STAMP" | aws s3 cp - $DEST/_DONE --region $REGION
shutdown -h now
