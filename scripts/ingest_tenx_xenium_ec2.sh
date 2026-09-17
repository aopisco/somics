#!/bin/bash
# Ingest the 10x Xenium outs bundles (specs/tenx_xenium) into the atlas, on a
# fresh box, unattended. Derived from ingest_tenx_visium_ec2.sh; the one
# structural difference is the source: a single outs zip per dataset (5-52 GB)
# from which only the members the builder reads are extracted -- cells.parquet,
# cell_feature_matrix.h5, experiment.xenium, metrics_summary.csv,
# gene_panel.json and the focus image (one file before Onboard Analysis 2.0, a
# morphology_focus/ directory of channels after). The zip itself is what gets
# staged to raw/.
#
# Starts from the atlas the 2026-09-02 rebuild produced and verified (236/237
# checks; the one miss is the published colon section's own defect, #19), so
# every section added here lands on a known-good base. For each spec in
# specs/tenx_xenium/, smallest first:
#
#   1. fetch the source files 10x's CDN serves (curl, browser UA, resumable)
#   2. stage them to s3://somics-dev/raw/<dataset_id>/ with a _manifest.json,
#      so the corpus grows by the same rule as everything else in raw/
#   3. build the package and ingest it (scripts/run_xenium_pipeline.sh)
#   4. sync the atlas to S3 -- the artifact survives the machine after every
#      dataset, not only at the end
#   5. free the scratch
#
# A failure before "== 6. ingest ==" is that dataset's problem: it is recorded
# in _failed.txt and the loop moves on. A failure inside ingest is fatal: a
# half-ingested dataset cannot be resumed (CLAUDE.md), so the run stops, syncs
# what it has under a FAILED marker, and shuts down for a human to look at.
#
# Run as EC2 user-data. Progress and logs land in s3://somics-dev/ingest/tenx_xenium/.
#
# Follow-up pass (e.g. the datasets a first run skipped, after a fix): use a
# wrapper as user-data that sets the overrides and runs this script --
#
#   #!/bin/bash
#   export SOMICS_BASE_ATLAS=s3://somics-dev/ingest/tenx_visium/atlas/<first run stamp>
#   export SOMICS_ONLY="tenx_a tenx_b"          # dataset_keys, space-separated
#   export SOMICS_BRANCH=main
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/ingest_tenx_xenium_ec2.sh | bash
#
# The base atlas already holds the first run's sections; somics.ingest refuses
# an overlapping section, so a dataset listed twice fails rather than doubling.
set -x
exec > /var/log/ingest.log 2>&1

# Family switch: the same fetch/stage/build/ingest loop serves any builder that
# implements --list-sources and any runner that takes SPEC (MERFISH reuses it).
FAMILY=${SOMICS_FAMILY:-tenx_xenium}
BUILDER=${SOMICS_BUILDER:-scripts/build_xenium_package.py}
RUNNER=${SOMICS_RUNNER:-scripts/run_xenium_pipeline.sh}
RAW_INCLUDE=${SOMICS_RAW_INCLUDE:-*/*_outs.zip}
B=s3://somics-dev/ingest/$FAMILY
BASE_ATLAS=${SOMICS_BASE_ATLAS:?set SOMICS_BASE_ATLAS to the newest ingest prefix}
ONLY=${SOMICS_ONLY:-}
SPEC_DIRS=${SOMICS_SPEC_DIRS:-specs/tenx_xenium}   # e.g. specs/hubmap_xenium
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
ATLAS_DEST=$B/atlas/$STAMP
REGION=us-east-1
BRANCH=${SOMICS_BRANCH:-protein-adapters}  # point at main once merged

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
# A SIGKILL (OOM) loses a block-buffered stdout; keep the last print in the log.
export PYTHONUNBUFFERED=1
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
# Specs whose sections the base atlas already holds are skipped here, before
# any fetch: a follow-up pass from a previous run's atlas then processes only
# what that run did not ingest, with no list to maintain.
ORDER=$(ONLY="$ONLY" SPEC_DIRS="$SPEC_DIRS" ATLAS="$ATLAS" uv run python - <<'PY'
import glob, json, os
import lancedb
only = set(os.environ.get("ONLY", "").split())
spec_dirs = os.environ.get("SPEC_DIRS", "specs/tenx_xenium").split()
db = lancedb.connect(os.path.join(os.environ["ATLAS"], "lance_db"))
names = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
names = list(getattr(names, "tables", names))  # lancedb >= 0.25 wraps the list
present = set()
if "TissueSectionSchema" in names:
    present = set(db.open_table("TissueSectionSchema").to_arrow().column("section_id").to_pylist())
specs = []
skipped = []
for p in sorted(p for d_ in spec_dirs for p in glob.glob(f"{d_}/*.json")):
    s = json.load(open(p))
    if only and s["dataset_key"] not in only:
        continue
    if s.get("samples"):
        done = all(e["section_id"] in present for e in s["samples"].values())
    elif s.get("source", {}).get("runs"):
        done = all(run in present for run in s["source"]["runs"])
    else:
        # MERFISH specs list no samples: the release's sections are derived at
        # build time and named '<study>.<n>', so the release is in when any is.
        # Allen section labels start with the release's feature_matrix_label
        # (C57BL6J-638850.37), which is not always the study name.
        stems = {s["study"], s.get("source", {}).get("feature_matrix_label") or s["study"]}
        done = any(sid == st or sid.startswith(st + ".") for sid in present for st in stems)
    if done:
        skipped.append(s["dataset_key"])
        continue
    specs.append((s["source"]["bytes"], p))
for _, p in sorted(specs):
    print(p)
import sys
print(f"{len(skipped)} spec(s) already in the base atlas: {skipped}", file=sys.stderr)
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
    if [[ "$URL" == s3://* ]]; then
      aws s3 cp "$URL" "$DEST" --region $REGION --only-show-errors || { echo "FETCH FAILED: $URL"; FETCH_OK=0; break; }
    else
      # figshare's ndownloader answers a browser UA with 202 and an empty body
      # and only redirects curl's own UA (to a signed S3 URL that expires in
      # 10 s, so no HEAD-then-GET); every other host here wants the browser UA.
      FUA=(-A "$UA"); [[ "$URL" == *ndownloader.figshare.com* ]] && FUA=()
      if ! curl -sSL "${FUA[@]}" --retry 8 --retry-all-errors --retry-delay 15 -C - -o "$DEST" "$URL"; then
        echo "FETCH FAILED: $URL"; FETCH_OK=0; break
      fi
      [ -s "$DEST" ] || { echo "FETCH FAILED (empty body): $URL"; FETCH_OK=0; break; }
    fi
    B0=$(stat -c %s "$DEST"); echo "  fetched $(basename $DEST) $((B0/1000000)) MB"
  done < <(uv run python $BUILDER --spec $SPEC --list-sources)
  T1=$(date +%s)
  if [ $FETCH_OK -eq 0 ]; then echo "$KEY	fetch" >> $D/failed.txt; continue; fi
  echo "  fetch took $((T1-T0)) s"

  # extract only what the builder reads; the zip keeps a top-level folder, so
  # -j flattens the loose members and the channel directory is moved whole
  EXTRACT_OK=1
  shopt -s nullglob
  for ZIP in $SOMICS_DATA_HOME/datasets/$KEY/extracted/*/*_outs.zip; do
    SDIR=$(dirname "$ZIP")
    # members vary by bundle: parquet + h5 (outs), or cells/matrix zarr archives
    # (Explorer and Atera bundles), with or without a top-level folder
    for M in cells.parquet cell_feature_matrix.h5 cells.zarr.zip cell_feature_matrix.zarr.zip experiment.xenium metrics_summary.csv gene_panel.json; do
      unzip -o -q -j "$ZIP" "*/$M" -d "$SDIR" 2>/dev/null || unzip -o -q -j "$ZIP" "$M" -d "$SDIR" 2>/dev/null || true
    done
    # unzip exits 1 on warnings (zip64 extra bytes on large archives) even when
    # it extracted, so judge by what landed, never by its exit code
    unzip -o -q -j "$ZIP" "*/morphology_focus.ome.tif" -d "$SDIR" 2>/dev/null || true
    [ -f "$SDIR/morphology_focus.ome.tif" ] || unzip -o -q -j "$ZIP" "morphology_focus.ome.tif" -d "$SDIR" 2>/dev/null || true
    # 1.x Explorer bundles carry the DAPI max projection instead of a focus image
    for M in "*/morphology_mip.ome.tif" "morphology_mip.ome.tif"; do
      [ -f "$SDIR/morphology_focus.ome.tif" ] || [ -f "$SDIR/morphology_mip.ome.tif" ] || unzip -o -q -j "$ZIP" "$M" -d "$SDIR" 2>/dev/null || true
    done
    if [ ! -f "$SDIR/morphology_focus.ome.tif" ] && [ ! -f "$SDIR/morphology_mip.ome.tif" ]; then
      rm -rf "$TMP/mf" && mkdir -p "$TMP/mf"
      unzip -o -q "$ZIP" "*/morphology_focus/*" -d "$TMP/mf" 2>/dev/null || true
      MFDIR=$(find "$TMP/mf" -type d -name morphology_focus | head -1)
      [ -z "$MFDIR" ] && { unzip -o -q "$ZIP" "morphology_focus/*" -d "$TMP/mf" 2>/dev/null || true; MFDIR=$(find "$TMP/mf" -type d -name morphology_focus | head -1); }
      if [ -n "$MFDIR" ] && [ -n "$(ls -A "$MFDIR")" ]; then rm -rf "$SDIR/morphology_focus" && mv "$MFDIR" "$SDIR/morphology_focus"; fi
    fi
    { [ -f "$SDIR/morphology_focus.ome.tif" ] || [ -f "$SDIR/morphology_mip.ome.tif" ] || [ -n "$(ls -A "$SDIR/morphology_focus" 2>/dev/null)" ]; } || EXTRACT_OK=0
    [ -f "$SDIR/experiment.xenium" ] || EXTRACT_OK=0
    { [ -f "$SDIR/cells.parquet" ] && [ -f "$SDIR/cell_feature_matrix.h5" ]; } \
      || { [ -f "$SDIR/cells.zarr.zip" ] && [ -f "$SDIR/cell_feature_matrix.zarr.zip" ]; } || EXTRACT_OK=0
  done
  shopt -u nullglob
  if [ $EXTRACT_OK -eq 0 ]; then echo "$KEY	extract" >> $D/failed.txt; rm -rf $SOMICS_DATA_HOME/datasets/$KEY; continue; fi

  # 2. stage raw to S3 with a manifest (source url, bytes, md5, fetch time) --
  #    unless every source is already an object in the bucket (HuBMAP)
  EXTRACTED=$SOMICS_DATA_HOME/datasets/$KEY/extracted
  if ! uv run python $BUILDER --spec $SPEC --list-sources | cut -f1 | grep -qv '^s3://somics-dev/'; then
    echo "  sources are in the bucket already; no raw staging"; T2=$(date +%s)
  else
  uv run python - "$SPEC" "$EXTRACTED" "$BUILDER" > $EXTRACTED/_manifest.json <<'PY' || fail
import hashlib, json, os, sys, datetime, importlib
spec, root = json.load(open(sys.argv[1])), sys.argv[2]
sys.path.insert(0, "scripts"); b = importlib.import_module(os.path.splitext(os.path.basename(sys.argv[3]))[0])
files = []
if spec.get("samples"):
    pairs = [(s, url, rel) for s in spec["samples"] for url, rel in b.sources_for(spec, s)]
else:
    pairs = [(None, url, rel) for url, rel in b.sources_for(spec)]
for sample, url, rel in pairs:
        p = os.path.join(root, sample, rel) if sample else os.path.join(root, rel); h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""): h.update(chunk)
        files.append({"key": f"{sample}/{rel}" if sample else rel, "source_url": url, "bytes": os.path.getsize(p),
                      "md5": h.hexdigest(), "fetched_at": datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.UTC).isoformat()})
json.dump({"dataset_id": spec["dataset_key"], "data_access_link": spec["data_access_link"],
           "staged_by": "scripts/ingest_tenx_xenium_ec2.sh", "files": files}, sys.stdout, indent=2)
PY
  aws s3 sync $EXTRACTED s3://somics-dev/raw/$KEY/ --region $REGION --only-show-errors \
      --exclude "*" --include "$RAW_INCLUDE" --include "_manifest.json" --exclude "*.part" \
      || echo "WARN: raw staging sync failed for $KEY"
  T2=$(date +%s); echo "  raw staging took $((T2-T1)) s"
  fi

  # 3. build + ingest
  if ! SPEC=$SPEC bash $RUNNER > $D/$KEY.log 2>&1; then
    tail -40 $D/$KEY.log
    dmesg 2>/dev/null | grep -iE "killed process|out of memory" | tail -3 | tee -a $D/$KEY.log
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
  aws s3 cp $SOMICS_DATA_HOME/datasets/$KEY/staging/sample_geometry.json $ATLAS_DEST/_geometry/$KEY.json --region $REGION --only-show-errors
  echo "$KEY	$((T1-T0))	$((T2-T1))	$((T3-T2))" >> $D/done.txt

  # 4. preserve, 5. free
  aws s3 sync $ATLAS $ATLAS_DEST --delete --exclude "_*" --region $REGION --only-show-errors || fail
  aws s3 cp $D/done.txt $ATLAS_DEST/_done.txt --region $REGION
  [ -f $D/failed.txt ] && aws s3 cp $D/failed.txt $ATLAS_DEST/_failed.txt --region $REGION
  aws s3 cp /var/log/ingest.log $ATLAS_DEST/_ingest.log --region $REGION
  rm -rf $SOMICS_DATA_HOME/datasets/$KEY $SOMICS_DATA_HOME/polycomb_data_packages/$KEY
  df -h $D | tail -1
done

# ---- repair: optimize() can leave a compacted obs fragment that fails filtered
# reads of the pointer structs; check, rewrite and snapshot if so ----------
PYTHONPATH=$D/repo/src uv run python scripts/repair_atlas.py --atlas $ATLAS --schema $SOMICS_SCHEMA > $D/repair.txt 2>&1; REPAIR_RC=$?
cat $D/repair.txt; aws s3 cp $D/repair.txt $ATLAS_DEST/_repair.txt --region $REGION
[ $REPAIR_RC -eq 0 ] || { echo "REPAIR FAILED (rc $REPAIR_RC)"; fail; }

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
