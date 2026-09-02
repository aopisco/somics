#!/bin/bash
# End-to-end rebuild of the Monkman NSCLC CODEX package and its ingest.
#
# Single-obs shape, like the Xenium runner: two feature spaces
# (protein_abundance, discrete_image) but only one obs table, because the
# composite image is a DATA file with no obs of its own. So finalization needs
# the same materialize_bare_obs bracket, and *not* reconcile_barcodes — there is
# only one modality to align.
#
# Three library tables, not four. This package has no DonorSchema at all: the
# deposit publishes TMA core positions with no core-to-patient mapping, so
# donor_uid stays null rather than minting one donor per core and asserting a
# cohort size the data does not support. The resolution pass therefore runs on
# TissueSectionSchema and PanelSchema only.
#
# Every step is idempotent, so a re-run after a fix is safe.
#
# Requires polycomb's skills:
#   curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash
#
# Run:
#     scripts/run_monkman_codex_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${SOMICS_SCHEMA:-$REPO/schema/spatial_transcriptomics_atlas_schema.yaml}"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
ROOT="$DATA_HOME/polycomb_data_packages/monkman_nsclc_codex"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
PY="${PYTHON:-python}"

echo "== 1. derive obs/var from the OME-TIFFs and the annotated h5ad =="
$PY "$REPO/scripts/build_monkman_codex_package.py"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_monkman_codex_collection.py"

echo "== 3. stage raw tables into Lance =="
$PY "$PREP/stage_lance_tables.py" "$ROOT" --schema "$SCHEMA"
for pair in "tissuesection_registry.csv:TissueSectionSchema" \
            "panel_registry.csv:PanelSchema" \
            "sectionimage_registry.csv:SectionImageSchema"; do
  $PY "$PREP/stage_library_table.py" "$ROOT" \
    --library "$ROOT/${pair%%:*}" --table "${pair##*:}"
done
$PY "$PREP/stage_dataset_table.py" "$ROOT" --schema "$SCHEMA"

echo "== 4. harmonize =="
for T in TissueSectionSchema PanelSchema; do
  $PY "$HARM/apply_resolution_pass.py" "$ROOT/lance_db" \
    --table "$T" --schema "$SCHEMA" --from-schema
done
$PY "$REPO/scripts/harmonize_monkman_registries.py"
$PY "$REPO/scripts/harmonize_monkman_datasets.py"

echo "== 5. finalize (note the bare/artifact bracket) =="
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT"

echo "== 7. verify against the source files =="
$PY "$REPO/scripts/verify_monkman_ingest.py"

echo
echo "Done. Diff against the published atlas with:"
echo "  $PY scripts/verify_rebuild_matches_atlas.py --rebuilt <atlas path>"
