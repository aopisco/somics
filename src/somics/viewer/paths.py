"""Filesystem locations the viewer needs to know about."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

# src/somics/viewer -> repo root. Only correct for an editable install from a checkout,
# which is how the viewer is meant to run; WEB_DIST simply won't exist otherwise and
# the API serves without a bundled frontend.
REPO_ROOT = PACKAGE_ROOT.parents[2]

WEB_DIST = REPO_ROOT / "viewer" / "dist"

# The corpus builder: a precomputed index of the atlas, and the app that browses it.
CORPUS_INDEX = REPO_ROOT / "data" / "corpus_index.json"
CORPUS_DIST = REPO_ROOT / "web" / "dist"

# One precomputed page per dataset, written by scripts/build_dataset_pages.py:
# a directory of PNG layers plus the HTML that browses them. Served as plain
# static files, so a page needs no atlas access and no Python at view time.
DATASET_PAGES = REPO_ROOT / "data" / "dataset_pages"
