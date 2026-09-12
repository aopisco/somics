#!/bin/bash
# Build + ingest a MERFISH release (Allen Brain Cell Atlas layout, or a staged
# Vizgen MERSCOPE bundle), spec-driven. The Xenium runner's steps with one
# structural difference: these sections have no image, so the collection has a
# single feature space (gene_expression). Staging then names the obs table bare
# and finalize_collection handles it directly -- the materialize_bare_obs
# bracket exists only for the two-space (expression + discrete_image) shape and
# is applied here only when the assembler did write a section-image registry.
#
# Run:
#     SPEC=specs/merfish/yao2023_merfish.json scripts/run_merfish_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${SOMICS_SCHEMA:-$REPO/schema/spatial_omics_atlas_schema.yaml}"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
SPEC="${SPEC:?set SPEC to a specs/merfish/*.json file}"
KEY="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" "$SPEC")"
ROOT="$DATA_HOME/polycomb_data_packages/$KEY"
STAGING="$DATA_HOME/datasets/$KEY/staging"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
PY="${PYTHON:-python}"
ATLAS_ARGS=()
[ -n "${SOMICS_ATLAS:-}" ] && ATLAS_ARGS=(--atlas "$SOMICS_ATLAS")

BUILD="${SOMICS_BUILD_SCRIPT:-$REPO/scripts/build_merfish_package.py}"   # seqFISH sets build_seqfish_package.py
echo "== 1. derive per-section obs/var/matrix from the release ($BUILD) =="
$PY "$BUILD" --spec "$SPEC"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_merfish_collection.py" --spec "$SPEC"

echo "== 3. stage raw tables into Lance =="
$PY "$PREP/stage_lance_tables.py" "$ROOT" --schema "$SCHEMA"
LIBS=("donor_registry.csv:DonorSchema" "tissuesection_registry.csv:TissueSectionSchema" "panel_registry.csv:PanelSchema")
# The assembler's coalesce(copy=False) MOVES the registries from staging into
# the package root, so look there (seqFISH run 5 checked staging, found
# nothing, skipped the bracket, and ingest found no finalized 'SpatialObs').
HAS_IMAGES=0
if [ -f "$ROOT/sectionimage_registry.csv" ] || [ -f "$STAGING/sectionimage_registry.csv" ]; then
  LIBS+=("sectionimage_registry.csv:SectionImageSchema"); HAS_IMAGES=1
fi
for pair in "${LIBS[@]}"; do
  $PY "$PREP/stage_library_table.py" "$ROOT" --library "$ROOT/${pair%%:*}" --table "${pair##*:}"
done
$PY "$PREP/stage_dataset_table.py" "$ROOT" --schema "$SCHEMA"

echo "== 4. harmonize =="
for T in DonorSchema TissueSectionSchema PanelSchema; do
  $PY "$HARM/apply_resolution_pass.py" "$ROOT/lance_db" --table "$T" --schema "$SCHEMA" --from-schema
done
$PY "$REPO/scripts/harmonize_merfish_package.py" --spec "$SPEC"

if [ "$HAS_IMAGES" -eq 1 ]; then
  echo "== 5. finalize (bare/artifact bracket: expression + image) =="
  $PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
  $PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
  $PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact
else
  echo "== 5. finalize (single feature space) =="
  $PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
fi

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT" "${ATLAS_ARGS[@]}"
echo "Done."
