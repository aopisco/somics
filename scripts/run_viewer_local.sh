#!/bin/bash
# Start the viewer API on the laptop with fresh SSO credentials.
#
# The API reads the private atlas with static credentials exported from the SSO
# profile, and those expire after about an hour: /api/samples and /points keep
# working from memory, but crops and gene painting start failing with 500s
# ("Failed to connect to namespace ... S3"). Rerun this script to refresh. The
# EC2 deployment (deploy_viewer_ec2.sh) uses an instance role and has no expiry.
#
#     scripts/run_viewer_local.sh [--port 8787]
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE=${SOMICS_AWS_PROFILE:-sci-data-dev-poweruser}
aws sts get-caller-identity --profile "$PROFILE" >/dev/null 2>&1 || { echo "SSO session for $PROFILE is not valid; run: aws sso login --profile $PROFILE (or the aws-oidc configure line in CLAUDE.md)"; exit 1; }
eval "$(aws configure export-credentials --profile "$PROFILE" --format env)"
export AWS_REGION=us-east-1
pkill -f "python -m somics.viewer" 2>/dev/null || true
sleep 1
echo "atlas: $(python3 -c "import json;print(json.load(open('data/atlas_pointer.json'))['atlas_dir'])")"
echo "viewer http://127.0.0.1:${2:-8787}  corpus http://127.0.0.1:${2:-8787}/corpus/  (credentials expire in ~1 h; rerun to refresh)"
exec uv run python -m somics.viewer "$@"
