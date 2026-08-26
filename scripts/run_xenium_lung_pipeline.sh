#!/bin/bash
# End-to-end rebuild of the Xenium lung preview package and its ingest.
#
# The step order matters in one non-obvious place. This dataset has two feature
# spaces but only one obs table (the section image is a DATA file with no obs of
# its own), so staging names the obs table SpatialObs_gene_expression while
# finalization only discovers tables by their exact schema class name.
# `materialize_bare_obs.py --phase bare` bridges that gap and **must run before**
# `finalize_collection.py`: finalization's cleanup drops the target-side
# `*_join` columns once every referrer is resolved, and an obs table that shows
# up afterwards has nothing left to join against.
#
# Every step is idempotent, so a re-run after a fix is safe.
#
# The staging, harmonization and finalization steps used to live in Claude
# skills on the hackathon box; they are reconstructed in scripts/pipeline/.
# Set SOMICS_DATA_HOME to run against data anywhere other than that box.
#
# Run:
#     scripts/run_xenium_lung_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="$REPO/schema/spatial_transcriptomics_atlas_schema.yaml"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
ROOT="$DATA_HOME/polycomb_data_packages/xenium_lung_preview"
PIPE="$REPO/scripts/pipeline"
PY="${PYTHON:-python}"

echo "== 1. derive obs/var from the outs bundles =="
$PY "$REPO/scripts/build_xenium_lung_package.py"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_xenium_lung_collection.py"

echo "== 3. stage raw tables into Lance =="
$PY "$PIPE/stage_lance_tables.py" "$ROOT" --schema "$SCHEMA"
for pair in "donor_registry.csv:DonorSchema" \
            "tissuesection_registry.csv:TissueSectionSchema" \
            "panel_registry.csv:PanelSchema" \
            "sectionimage_registry.csv:SectionImageSchema"; do
  $PY "$PIPE/stage_library_table.py" "$ROOT" \
    --library "$ROOT/${pair%%:*}" --table "${pair##*:}"
done
$PY "$PIPE/stage_dataset_table.py" "$ROOT" --schema "$SCHEMA"

echo "== 4. harmonize =="
# Ontology columns first, from the schema's own field markers. Note that
# --organism is not forwarded here: resolve_organisms takes no such kwarg.
for T in DonorSchema TissueSectionSchema PanelSchema; do
  $PY "$PIPE/apply_resolution_pass.py" "$ROOT/lance_db" \
    --table "$T" --schema "$SCHEMA" --from-schema
done
$PY "$REPO/scripts/harmonize_xenium_lung_registries.py"
$PY "$REPO/scripts/harmonize_xenium_lung_datasets.py"

echo "== 5. finalize (note the bare/artifact bracket) =="
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
$PY "$PIPE/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT"

echo "== 7. verify against the source files =="
$PY "$REPO/scripts/verify_xenium_lung_ingest.py"

echo
echo "Done. Publish with: scripts/sync_atlas_to_r2.sh"
