#!/bin/bash
# Ingest the 10x-catalogue Visium and Visium HD datasets into the verified atlas,
# on a fresh box, unattended.
#
# Starts from the atlas the 2026-09-02 rebuild produced and verified (236/237
# checks; the one miss is the published colon section's own defect, #19), so
# every section added here lands on a known-good base. For each spec in
# specs/tenx_visium/, smallest first:
#
#   1. fetch the source files 10x's CDN serves (curl, browser UA, resumable)
#   2. stage them to s3://somics-dev/raw/<dataset_id>/ with a _manifest.json,
#      so the corpus grows by the same rule as everything else in raw/
#   3. build the package and ingest it (scripts/run_visium_pipeline.sh)
#   4. sync the atlas to S3 -- the artifact survives the machine after every
#      dataset, not only at the end
#   5. free the scratch
#
# A failure before "== 6. ingest ==" is that dataset's problem: it is recorded
# in _failed.txt and the loop moves on. A failure inside ingest is fatal: a
# half-ingested dataset cannot be resumed (CLAUDE.md), so the run stops, syncs
# what it has under a FAILED marker, and shuts down for a human to look at.
#
# Run as EC2 user-data. Progress and logs land in s3://somics-dev/ingest/tenx_visium/.
#
# Follow-up pass (e.g. the datasets a first run skipped, after a fix): use a
# wrapper as user-data that sets the overrides and runs this script --
#
#   #!/bin/bash
#   export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/tenx_visium/atlas/<first run stamp>
#   export SOMICS_ONLY="tenx_a tenx_b"          # dataset_keys, space-separated
#   export SOMICS_BRANCH=main
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/ingest_tenx_visium_ec2.sh | bash
#
# The base atlas already holds the first run's sections; somics.ingest refuses
# an overlapping section, so a dataset listed twice fails rather than doubling.
set -x
exec > /var/log/ingest.log 2>&1

B=s3://somics-dev/ingest/tenx_visium
BASE_ATLAS=${SOMICS_BASE_ATLAS:-s3://somics-dev/rebuild/atlas/2026-09-02T00-43-52Z}
ONLY=${SOMICS_ONLY:-}
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
ATLAS_DEST=$B/atlas/$STAMP
REGION=us-east-1
BRANCH=${SOMICS_BRANCH:-tenx-visium-ingest}  # point at main once merged

fail() {
  aws s3 cp /var/log/ingest.log $B/ingest-FAILED-$STAMP.log --region $REGION
  [ -d "${ATLAS:-/nonexistent}/lance_db" ] && aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors
  [ -f "$D/failed.txt" ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
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
TMP=$D/scratch
mkdir -p $D $TMP && cd $D
git clone -b $BRANCH https://github.com/aopisco/somics.git repo || fail
cd repo && uv sync || fail
cd $D

# Reference cache, guarded by what landed (the `..` gotcha, CLAUDE.md).
aws s3 sync s3://somics-dev/polycomb/reference_db $D/reference_db --region $REGION --only-show-errors || fail
[ "$(du -sm $D/reference_db | cut -f1)" -gt 50000 ] || fail
(cd repo && uv run polycomb setup --db-path $D/reference_db) || fail

export SOMICS_DATA_HOME=$D/data
export POLYCOMB_SKILLS=/root/.agents/skills
export SOMICS_SCHEMA=$D/repo/schema/spatial_omics_atlas_schema.yaml
export PYTHON="uv run python"
mkdir -p $SOMICS_DATA_HOME

# ---- start from the verified atlas ---------------------------------------
ATLAS=$SOMICS_DATA_HOME/polycomb_atlases/somics_spatial_atlas
export SOMICS_ATLAS=$ATLAS
mkdir -p $ATLAS
aws s3 sync $BASE_ATLAS/ $ATLAS --exclude "_*" --region $REGION --only-show-errors || fail
[ "$(du -sm $ATLAS | cut -f1)" -gt 20000 ] || fail
echo "base atlas: $BASE_ATLAS" > $D/provenance.txt

# ---- run order: smallest first, a healthy human Visium as the smoke test ---
cd $D/repo
ORDER=$(ONLY="$ONLY" uv run python - <<'PY'
import glob, json, os
only = set(os.environ.get("ONLY", "").split())
specs = []
for p in sorted(glob.glob("specs/tenx_visium/*.json")):
    s = json.load(open(p))
    if only and s["dataset_key"] not in only:
        continue
    sample = next(iter(s["samples"].values()))
    smoke = not (s["technology"] == "visium" and s["organism"] == "Homo sapiens"
                 and sample["disease_state"] == "healthy")
    specs.append((smoke, s["technology"] == "visium_hd", s["source"]["bytes"], p))
for _, _, _, p in sorted(specs):
    print(p)
PY
) || fail
echo "$ORDER" > $D/order.txt
N=$(echo "$ORDER" | wc -l)

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
i=0
for SPEC in $ORDER; do
  i=$((i+1))
  KEY=$(uv run python -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" $SPEC)
  echo "### [$i/$N] $KEY  $(date -u +%FT%TZ)"
  T0=$(date +%s)

  # 1. fetch -- the builder's own destination names, so it finds them present
  FETCH_OK=1
  while IFS=$'\t' read -r URL DEST; do
    mkdir -p "$(dirname "$DEST")"
    if ! curl -sSL -A "$UA" --retry 8 --retry-all-errors --retry-delay 15 -C - -o "$DEST" "$URL"; then
      echo "FETCH FAILED: $URL"; FETCH_OK=0; break
    fi
    B0=$(stat -c %s "$DEST"); echo "  fetched $(basename $DEST) $((B0/1000000)) MB"
  done < <(uv run python scripts/build_visium_package.py --spec $SPEC --list-sources)
  T1=$(date +%s)
  if [ $FETCH_OK -eq 0 ]; then echo "$KEY	fetch" >> $D/failed.txt; continue; fi
  echo "  fetch took $((T1-T0)) s"

  # 2. stage raw to S3 with a manifest (source url, bytes, md5, fetch time)
  EXTRACTED=$SOMICS_DATA_HOME/datasets/$KEY/extracted
  uv run python - "$SPEC" "$EXTRACTED" > $EXTRACTED/_manifest.json <<'PY' || fail
import hashlib, json, os, sys, datetime
spec, root = json.load(open(sys.argv[1])), sys.argv[2]
sys.path.insert(0, "scripts"); import build_visium_package as b
files = []
for sample in spec["samples"]:
    for url, rel in b.sources_for(spec, sample):
        p = os.path.join(root, sample, rel); h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""): h.update(chunk)
        files.append({"key": f"{sample}/{rel}", "source_url": url, "bytes": os.path.getsize(p),
                      "md5": h.hexdigest(), "fetched_at": datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.UTC).isoformat()})
json.dump({"dataset_id": spec["dataset_key"], "data_access_link": spec["data_access_link"],
           "staged_by": "scripts/ingest_tenx_visium_ec2.sh", "files": files}, sys.stdout, indent=2)
PY
  aws s3 sync $EXTRACTED s3://somics-dev/raw/$KEY/ --region $REGION --only-show-errors \
      --exclude "*" --include "*/filtered_feature_bc_matrix.h5" --include "*/spatial.tar.gz" \
      --include "*/binned_outputs.tar.gz" --include "*/full_image.*" --include "_manifest.json" \
      --exclude "*.part" || echo "WARN: raw staging sync failed for $KEY"
  T2=$(date +%s); echo "  raw staging took $((T2-T1)) s"

  # 3. build + ingest
  if ! SPEC=$SPEC bash scripts/run_visium_pipeline.sh > $D/$KEY.log 2>&1; then
    tail -40 $D/$KEY.log
    aws s3 cp $D/$KEY.log $ATLAS_DEST/_logs/$KEY.log --region $REGION
    if grep -q "refusing to ingest" $D/$KEY.log; then
      # somics.ingest checks section uids before it writes anything: the atlas
      # is untouched and this dataset is a re-release of a section already in.
      echo "$KEY	duplicate section (already in the atlas)" >> $D/failed.txt
      rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
      continue
    fi
    if grep -q "== 6. ingest ==" $D/$KEY.log; then
      echo "FATAL: $KEY failed inside ingest; the atlas may hold a partial dataset"
      echo "$KEY	ingest (fatal)" >> $D/failed.txt
      fail
    fi
    echo "$KEY	build" >> $D/failed.txt
    rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
    continue
  fi
  T3=$(date +%s); echo "  build+ingest took $((T3-T2)) s"
  echo "$KEY	$((T1-T0))	$((T2-T1))	$((T3-T2))" >> $D/done.txt

  # 4. preserve, 5. free
  aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors || fail
  aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
  [ -f $D/failed.txt ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
  aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
  rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
  df -h $D | tail -1
done

# ---- finish -----------------------------------------------------------------
aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors || fail
aws s3 cp $D/provenance.txt $ATLAS_DEST/_provenance.txt --region $REGION
aws s3 cp $D/order.txt $ATLAS_DEST/_order.txt --region $REGION
[ -f $D/done.txt ] && aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
[ -f $D/failed.txt ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
echo "DONE $STAMP $(wc -l < $D/done.txt) ingested, $(wc -l < $D/failed.txt 2>/dev/null || echo 0) failed" \
  | aws s3 cp - $ATLAS_DEST/_DONE --region $REGION
shutdown -h now
