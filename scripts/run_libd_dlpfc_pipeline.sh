#!/bin/bash
# The LIBD DLPFC Visium study, through the generic Visium runner.
#
# Kept as an entry point because the rebuild script and CLAUDE.md name it; the
# pipeline itself lives in run_visium_pipeline.sh, which serves any Visium or
# Visium HD spec.
#
# Run:
#     scripts/run_libd_dlpfc_pipeline.sh            # specs/libd_dlpfc.json
#     SPEC=specs/other.json scripts/run_libd_dlpfc_pipeline.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="${SPEC:-$REPO/specs/libd_dlpfc.json}" exec "$REPO/scripts/run_visium_pipeline.sh"
