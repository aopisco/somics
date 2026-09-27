#!/bin/bash
# End-to-end build and ingest of one HuBMAP MIBI lab-submission dataset, spec-driven.
#
# Single-obs shape, like the Monkman CODEX runner: two feature spaces
# (protein_abundance, discrete_image) but one obs table, because the ion-count
# stack is a DATA file with no obs of its own. So finalization needs the
# materialize_bare_obs bracket and *not* reconcile_barcodes -- there is only one
# modality to align.
#
# Four library tables: donors, tissue sections, the antibody panel, and section
# images. The resolution pass runs on the three that carry ontology columns.
#
# Run:
#     SPEC=specs/mibi/<dataset>.json scripts/run_mibi_pipeline.sh
# Env: SOMICS_SCHEMA, SOMICS_DATA_HOME, SOMICS_ATLAS (passed to somics.ingest
# --atlas), POLYCOMB_SKILLS, PYTHON. Source files are fetched from
# s3://somics-dev/hubmap/ by the builder if absent (AWS credentials required).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${SOMICS_SCHEMA:-$REPO/schema/spatial_omics_atlas_schema.yaml}"
DATA_HOME="${SOMICS_DATA_HOME:-/home/ubuntu}"
: "${SPEC:?set SPEC=<spec.json>}"
KEY="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dataset_key'])" "$SPEC")"
ROOT="$DATA_HOME/polycomb_data_packages/$KEY"
SKILLS="${POLYCOMB_SKILLS:-$HOME/.agents/skills}"
PREP="$SKILLS/prepare-package-for-resolution/scripts"
HARM="$SKILLS/schema-harmonization/scripts"
FIN="$SKILLS/finalize-tables/scripts"
PY="${PYTHON:-python}"
ATLAS_ARGS=()
[ -n "${SOMICS_ATLAS:-}" ] && ATLAS_ARGS=(--atlas "$SOMICS_ATLAS")

echo "== 1. per-cell counts, geometry and the channels-last stack =="
$PY "$REPO/scripts/build_mibi_package.py" --spec "$SPEC"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_mibi_collection.py" --spec "$SPEC"

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
$PY "$REPO/scripts/harmonize_mibi_package.py" --spec "$SPEC"

echo "== 5. finalize (note the bare/artifact bracket) =="
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT" "${ATLAS_ARGS[@]}"
