#!/bin/bash
# Ingest HuBMAP proteomics-imaging packages (SPRM: CODEX/PhenoCycler; MIBI lab
# submissions) into the somics atlas, on a fresh box, unattended.
#
# The sibling of ingest_tenx_visium_ec2.sh with two differences: sources are
# already in s3://somics-dev/hubmap/ (the builders fetch them; nothing is staged
# to raw/), and each spec's family picks its runner by the spec's directory
# (specs/sprm -> run_sprm_pipeline.sh, specs/mibi -> run_mibi_pipeline.sh).
#
# One atlas, serial: SOMICS_BASE_ATLAS is required and should be the newest
# ingest prefix (the Visium block's), so this run stacks on it rather than
# forking the atlas. Specs whose sections the base already holds are skipped
# before any fetch, so a re-run processes only what is missing.
#
# Failure handling as in the Visium script: a failure before "== 6. ingest =="
# skips that dataset; "refusing to ingest" is a duplicate section and skips; a
# failure inside ingest is fatal (partial dataset, not resumable) and stops the
# run under a FAILED marker.
#
# Wrapper user-data for a run:
#
#   #!/bin/bash
#   export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/tenx_visium/atlas/<stamp>
#   export SOMICS_ONLY="hubmap_hbm626_kxrz_238_unspecified_spleen_na hubmap_hbm526_ffgj_297_mibi_uterus_protein"
#   export SOMICS_BRANCH=protein-adapters
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/ingest_protein_ec2.sh | bash
#
# Progress and logs land in s3://somics-dev/ingest/protein/.
set -x
exec > /var/log/ingest.log 2>&1

B=s3://somics-dev/ingest/protein
BASE_ATLAS=${SOMICS_BASE_ATLAS:?set SOMICS_BASE_ATLAS to the newest ingest prefix}
ONLY=${SOMICS_ONLY:-}
FAMILIES=${SOMICS_FAMILIES:-sprm mibi}
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
ATLAS_DEST=$B/atlas/$STAMP
REGION=us-east-1
BRANCH=${SOMICS_BRANCH:-protein-adapters}

fail() {
  aws s3 cp /var/log/ingest.log $B/ingest-FAILED-$STAMP.log --region $REGION
  [ -d "${ATLAS:-/nonexistent}/lance_db" ] && aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors
  [ -f "$D/failed.txt" ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
  [ -f "$D/done.txt" ] && aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
  aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
  echo "FAILED $STAMP" | aws s3 cp - $ATLAS_DEST/_FAILED --region $REGION
  shutdown -h now
  exit 1
}

dnf install -y git unzip tmux
export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash

D=/mnt/work
mkdir -p $D && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || fail
cd repo && uv sync || fail
cd $D

aws s3 sync s3://somics-dev/polycomb/reference_db $D/reference_db --region $REGION --only-show-errors || fail
[ "$(du -sm $D/reference_db | cut -f1)" -gt 50000 ] || fail
(cd repo && uv run polycomb setup --db-path $D/reference_db) || fail

export SOMICS_DATA_HOME=$D/data
export POLYCOMB_SKILLS=/root/.agents/skills
export SOMICS_SCHEMA=$D/repo/schema/spatial_omics_atlas_schema.yaml
export PYTHON="uv run python"
mkdir -p $SOMICS_DATA_HOME

ATLAS=$SOMICS_DATA_HOME/polycomb_atlases/somics_spatial_atlas
export SOMICS_ATLAS=$ATLAS
mkdir -p $ATLAS
aws s3 sync $BASE_ATLAS/ $ATLAS --exclude "_*" --region $REGION --only-show-errors || fail
[ "$(du -sm $ATLAS | cut -f1)" -gt 20000 ] || fail
echo "base atlas: $BASE_ATLAS" > $D/provenance.txt

cd $D/repo
ORDER=$(ONLY="$ONLY" FAMILIES="$FAMILIES" ATLAS="$ATLAS" uv run python - <<'PY'
import glob, json, os, sys
import lancedb
only = set(os.environ.get("ONLY", "").split())
db = lancedb.connect(os.path.join(os.environ["ATLAS"], "lance_db"))
names = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
names = list(getattr(names, "tables", names))
present = set()
if "TissueSectionSchema" in names:
    present = set(db.open_table("TissueSectionSchema").to_arrow().column("section_id").to_pylist())
specs, skipped = [], []
# MIBI first: small (0.6 GB) and uniform, so a pipeline problem shows in minutes.
for fam in ("mibi", "sprm"):
    if fam not in os.environ.get("FAMILIES", "").split():
        continue
    for p in sorted(glob.glob(f"specs/{fam}/*.json")):
        s = json.load(open(p))
        if only and s["dataset_key"] not in only:
            continue
        if all(e["section_id"] in present for e in s["samples"].values()):
            skipped.append(s["dataset_key"]); continue
        specs.append(p)
for p in specs:
    print(p)
print(f"{len(skipped)} spec(s) already in the base atlas: {skipped}", file=sys.stderr)
PY
) || fail
echo "$ORDER" > $D/order.txt
N=$(echo "$ORDER" | grep -c . || true)

i=0
for SPEC in $ORDER; do
  i=$((i+1))
  KEY=$(uv run python -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" $SPEC)
  FAM=$(basename "$(dirname "$SPEC")")
  echo "### [$i/$N] $FAM $KEY  $(date -u +%FT%TZ)"
  T0=$(date +%s)
  if ! SPEC=$SPEC bash scripts/run_${FAM}_pipeline.sh > $D/$KEY.log 2>&1; then
    tail -40 $D/$KEY.log
    aws s3 cp $D/$KEY.log $ATLAS_DEST/_logs/$KEY.log --region $REGION
    if grep -q "refusing to ingest" $D/$KEY.log; then
      echo "$KEY	duplicate section (already in the atlas)" >> $D/failed.txt
    elif grep -q "== 6. ingest ==" $D/$KEY.log; then
      echo "FATAL: $KEY failed inside ingest; the atlas may hold a partial dataset"
      echo "$KEY	ingest (fatal)" >> $D/failed.txt
      fail
    else
      echo "$KEY	build" >> $D/failed.txt
    fi
    rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
    continue
  fi
  T1=$(date +%s); echo "  build+ingest took $((T1-T0)) s"
  echo "$KEY	$FAM	$((T1-T0))" >> $D/done.txt
  aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors || fail
  aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
  [ -f $D/failed.txt ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
  aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
  rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
  df -h $D | tail -1
done

aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors || fail
aws s3 cp $D/provenance.txt $ATLAS_DEST/_provenance.txt --region $REGION
aws s3 cp $D/order.txt $ATLAS_DEST/_order.txt --region $REGION
[ -f $D/done.txt ] && aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
[ -f $D/failed.txt ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
echo "DONE $STAMP $(wc -l < $D/done.txt 2>/dev/null || echo 0) ingested, $(wc -l < $D/failed.txt 2>/dev/null || echo 0) failed" \
  | aws s3 cp - $ATLAS_DEST/_DONE --region $REGION
shutdown -h now
