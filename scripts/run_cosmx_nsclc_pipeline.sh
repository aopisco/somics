#!/bin/bash
# End-to-end rebuild of the CosMx NSCLC package and its ingest.
#
# This is the two-obs-table path, and that is the whole reason it differs from
# the Xenium runner. CosMx measures gene expression *and* protein on the same
# cells, so staging writes SpatialObs_gene_expression and
# SpatialObs_protein_abundance, and finalization needs three extra steps around
# it that a single-modality dataset does not:
#
#   reconcile_barcodes.py   writes multimodal_barcode on each per-space obs
#                           table, choosing the normalization that maximises
#                           cross-modality overlap
#
# That is the only extra step. Everything else multimodal -- joining the
# per-space obs tables and stamping the finalized uid back onto them -- is
# already inside finalize_collection, unlike the Xenium runner where
# materialize_bare_obs has to bracket it.
#
# Every step is idempotent, so a re-run after a fix is safe.
#
# Requires polycomb's skills:
#   curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash
#
# Run:
#     scripts/run_cosmx_nsclc_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="$REPO/schema/spatial_transcriptomics_atlas_schema.yaml"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
ROOT="$DATA_HOME/polycomb_data_packages/cosmx_nsclc_ffpe"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
ALIGN="$SKILLS/multimodal-alignment/scripts"
PY="${PYTHON:-python}"

echo "== 1. derive obs/var from the flat files =="
$PY "$REPO/scripts/build_cosmx_nsclc_package.py"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_cosmx_nsclc_collection.py"

echo "== 3. stage raw tables into Lance =="
$PY "$PREP/stage_lance_tables.py" "$ROOT" --schema "$SCHEMA"
for pair in "donor_registry.csv:DonorSchema" \
            "tissuesection_registry.csv:TissueSectionSchema" \
            "panel_registry.csv:PanelSchema" \
            "sectionimage_registry.csv:SectionImageSchema"; do
  $PY "$PREP/stage_library_table.py" "$ROOT" \
    --library "$ROOT/${pair%%:*}" --table "${pair##*:}"
done
$PY "$PREP/stage_dataset_table.py" "$ROOT" --schema "$SCHEMA"

echo "== 4. harmonize =="
for T in DonorSchema TissueSectionSchema PanelSchema; do
  $PY "$HARM/apply_resolution_pass.py" "$ROOT/lance_db" \
    --table "$T" --schema "$SCHEMA" --from-schema
done
$PY "$REPO/scripts/harmonize_cosmx_registries.py"
$PY "$REPO/scripts/harmonize_cosmx_datasets.py"

echo "== 5. reconcile barcodes across the two modalities =="
for db in "$ROOT"/*/lance_db; do
  [ -d "$db" ] || continue
  $PY "$ALIGN/reconcile_barcodes.py" "$db" --obs-class SpatialObs
done

echo "== 6. finalize =="
# finalize_collection does the whole sequence itself, including the pieces a
# multimodal dataset needs: join feature-space obs, assign uids, stamp uid back
# onto each per-space table (its step 1b), set dataset_uid, populate registry
# keys, compute derived columns, drop leftovers, validate.
#
# Do not call join_feature_space_obs, assign_uids or
# stamp_uid_on_feature_space_obs separately around it. Those are for
# table-by-table debugging; running them alongside the orchestrator double-runs
# steps and breaks it -- the stamp replaces each per-space table with a uid-only
# artifact, so a second join finds no barcodes to join on.
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"

echo "== 7. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT"

echo "== 8. verify against the source files =="
$PY "$REPO/scripts/verify_cosmx_ingest.py"

echo
echo "Done. Diff against the published atlas with:"
echo "  $PY scripts/verify_rebuild_matches_atlas.py --rebuilt <atlas path>"
