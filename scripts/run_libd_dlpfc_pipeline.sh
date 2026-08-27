#!/bin/bash
# End-to-end rebuild of a Visium study and its ingest, spec-driven.
#
# Single-obs shape: two feature spaces (gene_expression, discrete_image) but one
# obs table, because the H&E is a DATA file with no obs of its own. So the
# materialize_bare_obs bracket applies, as it does for Xenium and Monkman.
#
# Three library tables, not four. Visium is whole-transcriptome, so there is no
# panel to register and panel_uid stays null. The resolution pass therefore runs
# on DonorSchema and TissueSectionSchema only.
#
# Run:
#     SPEC=specs/libd_dlpfc.json scripts/run_libd_dlpfc_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${SOMICS_SCHEMA:-$REPO/schema/spatial_transcriptomics_atlas_schema.yaml}"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
SPEC="${SPEC:-$REPO/specs/libd_dlpfc.json}"
KEY="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" "$SPEC")"
ROOT="$DATA_HOME/polycomb_data_packages/$KEY"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
PY="${PYTHON:-python}"

echo "== 1. fetch sources and derive obs/var =="
$PY "$REPO/scripts/build_visium_package.py" --spec "$SPEC"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_visium_collection.py" --spec "$SPEC"

echo "== 3. stage raw tables into Lance =="
$PY "$PREP/stage_lance_tables.py" "$ROOT" --schema "$SCHEMA"
for pair in "donor_registry.csv:DonorSchema" \
            "tissuesection_registry.csv:TissueSectionSchema" \
            "sectionimage_registry.csv:SectionImageSchema"; do
  $PY "$PREP/stage_library_table.py" "$ROOT" \
    --library "$ROOT/${pair%%:*}" --table "${pair##*:}"
done
$PY "$PREP/stage_dataset_table.py" "$ROOT" --schema "$SCHEMA"

echo "== 4. harmonize =="
for T in DonorSchema TissueSectionSchema; do
  $PY "$HARM/apply_resolution_pass.py" "$ROOT/lance_db" \
    --table "$T" --schema "$SCHEMA" --from-schema
done
$PY "$REPO/scripts/harmonize_visium_package.py" --spec "$SPEC"

echo "== 5. finalize (note the bare/artifact bracket) =="
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT"

echo
echo "Done. Diff against the published atlas with:"
echo "  $PY scripts/verify_rebuild_matches_atlas.py --rebuilt <atlas path>"
