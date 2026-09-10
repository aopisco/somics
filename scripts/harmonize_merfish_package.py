#!/usr/bin/env python3
"""Harmonize a MERFISH package to the atlas schema.

The per-table work is the Xenium harmonizer's: the builder writes the feature
table in 10x's vocabulary (``Gene Expression`` / ``Blank Codeword``) and the
same obs columns, so ``harmonize_xenium_package.harmonize_sample`` applies
verbatim. What differs is where the per-section facts live: a MERFISH spec has
no ``samples`` block (sections are derived from the release's cell metadata at
build time), so the section entries the Xenium harmonizer expects are
synthesised here from ``sample_geometry.json`` and the spec's dataset-level
disease status.

Run:
    python scripts/harmonize_merfish_package.py --spec specs/merfish/<dataset>.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harmonize_xenium_package import harmonize_sample  # noqa: E402

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--package")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    package = args.package or os.path.join(DATA_HOME, "polycomb_data_packages", key)
    geometry = json.load(open(os.path.join(DATA_HOME, "datasets", key, "staging", "sample_geometry.json")))
    spec["samples"] = {
        g["sample"]: {
            "section_id": g["section_id"],
            "donor_id": g["donor_id"],
            "disease_state": spec["disease_state"],
            "disease": spec.get("disease"),
        }
        for g in geometry
    }
    for g in geometry:
        # A family can mix units (Liu 2022: segmented runs are cells, the
        # unsegmented ones grid bins); the builder records each run's unit.
        per = dict(spec)
        per["spatial_unit"] = g.get("spatial_unit", spec["spatial_unit"])
        per["segmentation_method"] = g.get("segmentation_method", spec["segmentation_method"])
        harmonize_sample(per, package, g["sample"], args.dry_run)


if __name__ == "__main__":
    main()
