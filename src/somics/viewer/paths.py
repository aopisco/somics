"""Filesystem locations the viewer needs to know about."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

# src/somics/viewer -> repo root. Only correct for an editable install from a checkout,
# which is how the viewer is meant to run; WEB_DIST simply won't exist otherwise and
# the API serves without a bundled frontend.
REPO_ROOT = PACKAGE_ROOT.parents[2]

WEB_DIST = REPO_ROOT / "viewer" / "dist"
