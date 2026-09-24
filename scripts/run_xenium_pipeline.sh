#!/bin/bash
# End-to-end rebuild of a Xenium study and its ingest, spec-driven.
#
# The generalised form of run_xenium_lung_pipeline.sh: same steps, but the
# study's identity and curation come from a spec rather than module constants,
# so one runner covers any Xenium outs bundle.
#
# Single-obs shape: two feature spaces (gene_expression, discrete_image) but one
# obs table, because the morphology image is a DATA file with no obs of its own.
# Hence the materialize_bare_obs bracket around finalization -- bare before,
# because cleanup drops the target-side *_join columns once every referrer is
# resolved, and artifact after, because it needs the uids finalization assigns.
#
# Four library tables, unlike Visium's three: Xenium is a targeted assay, so the
# panel is part of the dataset's identity and gets a registry.
#
# Run:
#     SPEC=specs/xenium_colon_preview.json scripts/run_xenium_pipeline.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${SOMICS_SCHEMA:-$REPO/schema/spatial_transcriptomics_atlas_schema.yaml}"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
SPEC="${SPEC:?set SPEC to a specs/*.json file}"
KEY="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" "$SPEC")"
ROOT="$DATA_HOME/polycomb_data_packages/$KEY"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
PY="${PYTHON:-python}"
ATLAS_ARGS=()
[ -n "${SOMICS_ATLAS:-}" ] && ATLAS_ARGS=(--atlas "$SOMICS_ATLAS")

echo "== 1. derive obs/var from the outs bundle =="
$PY "$REPO/scripts/build_xenium_package.py" --spec "$SPEC"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_xenium_collection.py" --spec "$SPEC"

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
$PY "$REPO/scripts/harmonize_xenium_package.py" --spec "$SPEC"

if ls "$ROOT"/*/lance_db/SpatialObs_protein_abundance.lance >/dev/null 2>&1; then
  # Co-detection bundle: two feature spaces on the same cells. The multimodal
  # shape (CosMx): reconcile barcodes, then finalize_collection alone -- it
  # joins the per-space obs tables and stamps uids itself (CLAUDE.md).
  echo "== 5. reconcile barcodes across gene and protein =="
  ALIGN="$SKILLS/multimodal-alignment/scripts"
  for db in "$ROOT"/*/lance_db; do
    [ -d "$db" ] || continue
    $PY "$ALIGN/reconcile_barcodes.py" "$db" --obs-class SpatialObs
  done
  echo "== 6. finalize =="
  $PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
else
  echo "== 5. finalize (note the bare/artifact bracket) =="
  $PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
  $PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
  $PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact
fi

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT" "${ATLAS_ARGS[@]}"

echo
echo "Done. Diff against the published atlas with:"
echo "  $PY scripts/verify_rebuild_matches_atlas.py --rebuilt <atlas path>"
