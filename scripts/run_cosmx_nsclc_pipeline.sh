#!/bin/bash
# End-to-end rebuild of the CosMx NSCLC package and its ingest.
#
# This is the two-obs-table path, and that is the whole reason it differs from
# the Xenium runner. CosMx measures gene expression *and* protein on the same
# cells, so staging writes SpatialObs_gene_expression and
# SpatialObs_protein_abundance, and finalization needs three extra steps around
# it that a single-modality dataset does not:
#
#   reconcile_barcodes.py       writes multimodal_barcode on each per-space obs
#                               table, choosing the normalization that maximises
#                               cross-modality overlap
#   join_feature_space_obs.py   outer-joins them into the bare obs class name,
#                               keeping the per-space tables for ingestion
#   stamp_uid_on_feature_space_obs.py
#                               copies the finalized uid back onto each per-space
#                               table in DATA row order, which is what ingestion
#                               aligns emitted matrix rows through
#
# The bracket around finalize_collection.py is load-bearing in the same way as
# the Xenium runner's: the join must precede it (finalization discovers obs by
# exact class name) and the stamp must follow it (it copies uids that
# finalization assigns).
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

echo "== 6. finalize (join, assign uids, stamp, then the rest) =="
# The stamp has to sit between assign_uids and the rest of finalization, not
# after it. It copies each finalized uid back onto the per-space tables by
# joining on multimodal_barcode -- and drop_leftover_columns, which runs inside
# finalize_collection, removes multimodal_barcode because it is not a schema
# field. Running the whole of finalization first leaves the stamp nothing to
# join on. assign_uids is idempotent, so finalize_collection re-running it is
# harmless.
$PY "$FIN/join_feature_space_obs.py" "$ROOT" --obs-class SpatialObs
$PY "$FIN/assign_uids.py" "$ROOT" --schema "$SCHEMA"
$PY "$FIN/stamp_uid_on_feature_space_obs.py" "$ROOT" --obs-class SpatialObs
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"

echo "== 7. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT"

echo "== 8. verify against the source files =="
$PY "$REPO/scripts/verify_cosmx_ingest.py"

echo
echo "Done. Diff against the published atlas with:"
echo "  $PY scripts/verify_rebuild_matches_atlas.py --rebuilt <atlas path>"
