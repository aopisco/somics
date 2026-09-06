#!/bin/bash
# Run scripts/verify_visium_ingest.py against an atlas prefix from EC2, where the
# instance role never expires and S3 is local. A laptop run of all 78 Visium
# specs died after an hour when the exported SSO token expired mid-read.
#
# Wrapper user-data:
#   #!/bin/bash
#   export SOMICS_ATLAS_PREFIX=s3://somics-dev/ingest/<family>/atlas/<stamp>
#   export SOMICS_SPECS="specs/tenx_visium/*.json specs/tenx_xenium/*.json"   # space-separated globs
#   export SOMICS_BRANCH=protein-adapters
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/verify_atlas_ec2.sh | bash
#
# The report and crop grids land under <prefix>/_verify/<stamp>/.
set -x
exec > /var/log/verify.log 2>&1
PREFIX=${SOMICS_ATLAS_PREFIX:?set SOMICS_ATLAS_PREFIX}
SPECS=${SOMICS_SPECS:-specs/tenx_visium/*.json}
BRANCH=${SOMICS_BRANCH:-main}
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
DEST=$PREFIX/_verify/$STAMP
REGION=us-east-1
export AWS_REGION=$REGION
finish() { aws s3 cp /var/log/verify.log $DEST/_verify.log --region $REGION; shutdown -h now; }

dnf install -y git
export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
D=/mnt/work; mkdir -p $D && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || finish
cd repo && uv sync || finish

RC=0
for G in $SPECS; do
  NAME=$(basename "$(dirname "$G")")
  uv run --with s3fs python scripts/verify_visium_ingest.py --atlas "$PREFIX" --specs "$G" --source-check \
      --report "$D/verify_$NAME.md" --crops-dir "$D/crops_$NAME" > "$D/verify_$NAME.log" 2>&1 || RC=1
  tail -5 "$D/verify_$NAME.log"
  aws s3 cp "$D/verify_$NAME.md" $DEST/verify_$NAME.md --region $REGION
  aws s3 cp "$D/verify_$NAME.log" $DEST/verify_$NAME.log --region $REGION
  aws s3 sync "$D/crops_$NAME" $DEST/crops_$NAME --region $REGION --only-show-errors
done
echo "$([ $RC -eq 0 ] && echo PASSED || echo HAS_FAILURES) $STAMP" | aws s3 cp - $DEST/_RESULT --region $REGION
finish
