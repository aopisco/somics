#!/bin/bash
# End-to-end build and ingest of one HuBMAP Cytokit + SPRM dataset (CODEX or
# PhenoCycler), spec-driven.
#
# Single-obs shape, like the Monkman CODEX runner: two feature spaces
# (protein_abundance, discrete_image) but one obs table, because the stitched
# expression stack is a DATA file with no obs of its own. So the
# materialize_bare_obs bracket applies, and reconcile_barcodes does not.
#
# Four library tables: donors, tissue sections, the antibody panel, and section
# images -- HuBMAP publishes a donor per dataset and Cytokit's extract is a
# targeted panel, so unlike Monkman there is a DonorSchema and unlike Visium
# there is a PanelSchema. The resolution pass runs on all three ontology-bearing
# ones.
#
# Run:
#     SPEC=specs/sprm/<dataset>.json scripts/run_sprm_pipeline.sh
# Env: SOMICS_SCHEMA, SOMICS_DATA_HOME, SOMICS_ATLAS (passed to somics.ingest
# --atlas), POLYCOMB_SKILLS, PYTHON. Sources are fetched from S3 by the builder;
# the box needs read access to s3://somics-dev/hubmap/.
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

echo "== 1. fetch sources, derive obs/var/matrix, rewrite the image channels-last =="
$PY "$REPO/scripts/build_sprm_package.py" --spec "$SPEC"

echo "== 2. registries + collection.json =="
$PY "$REPO/scripts/assemble_sprm_collection.py" --spec "$SPEC"

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
$PY "$REPO/scripts/harmonize_sprm_package.py" --spec "$SPEC"

echo "== 5. finalize (note the bare/artifact bracket) =="
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase bare
$PY "$FIN/finalize_collection.py" "$ROOT" --schema "$SCHEMA"
$PY "$REPO/scripts/materialize_bare_obs.py" "$ROOT" --obs-class SpatialObs --phase artifact

echo "== 6. ingest =="
PYTHONPATH="$REPO/src" $PY -m somics.ingest "$ROOT" "${ATLAS_ARGS[@]}"

echo
echo "Done. Read the sections back with scripts/verify_visium_ingest.py's sibling once one exists;"
echo "until then, check obs rows per section against sample_geometry.json n_cells."
