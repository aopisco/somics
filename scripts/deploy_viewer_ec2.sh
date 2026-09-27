#!/bin/bash
# Provision the somics viewer app server (EC2 user-data), private-subnet, no
# inbound except from the load balancer's security group.
#
# What it does: clone the branch, install uv + node, build both UI bundles
# (viewer/dist at /, web/dist at /corpus/), and run the FastAPI process as a
# systemd service on 0.0.0.0:8787. The API reads the atlas prefix and viewer
# index named by data/atlas_pointer.json with the instance role (somics-raw-staging
# reads s3://somics-dev). Health check: GET /api/anatomy.
#
# Redeploy (new commit or new atlas pointer): `sudo systemctl restart somics-viewer`
# after `git pull` in /opt/somics/repo, or `bash /opt/somics/redeploy.sh` which
# pulls, rebuilds the UIs and restarts.
#
# Run as user-data:
#   export SOMICS_BRANCH=protein-adapters
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/$SOMICS_BRANCH/scripts/deploy_viewer_ec2.sh | bash
set -euo pipefail
exec > /var/log/somics-viewer-deploy.log 2>&1
BRANCH=${SOMICS_BRANCH:-main}
export HOME=/root
dnf install -y git nodejs20 npm
alternatives --set node /usr/bin/node-20 2>/dev/null || true
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
mkdir -p /opt/somics && cd /opt/somics
[ -d repo ] || git clone -b "$BRANCH" https://github.com/aopisco/somics.git repo
cd repo && git checkout "$BRANCH" && git pull --ff-only
uv sync
(cd viewer && npm ci && npm run build)
(cd web && npm ci && npm run build)

cat > /opt/somics/redeploy.sh <<'EOF'
#!/bin/bash
set -euo pipefail
export PATH="/root/.local/bin:$PATH"
cd /opt/somics/repo && git pull --ff-only && uv sync
(cd viewer && npm ci && npm run build) && (cd web && npm ci && npm run build)
systemctl restart somics-viewer && systemctl --no-pager status somics-viewer | head -5
EOF
chmod +x /opt/somics/redeploy.sh

cat > /etc/systemd/system/somics-viewer.service <<'EOF'
[Unit]
Description=somics atlas viewer API (FastAPI, serves viewer/dist and web/dist)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/somics/repo
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=AWS_REGION=us-east-1
Environment=SOMICS_VIEWER_CACHE=/var/cache/somics-viewer
ExecStart=/root/.local/bin/uv run python -m somics.viewer --host 0.0.0.0 --port 8787
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
mkdir -p /var/cache/somics-viewer
systemctl daemon-reload
systemctl enable --now somics-viewer
sleep 15
curl -fsS -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8787/api/anatomy || { echo "API did not come up"; journalctl -u somics-viewer --no-pager | tail -30; exit 1; }
echo "somics viewer deployed on $(hostname -I | awk '{print $1}'):8787"
