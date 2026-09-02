#!/usr/bin/env python3
"""Assemble a 10x Visium study into a data package, driven by a spec file.

Visium is the third builder shape in this repo and the first whole-transcriptome
one, which changes two things worth stating up front:

- **There is no panel.** Visium measures the transcriptome, so the package
  writes no panel registry and `panel_uid` stays null. Every other family here
  is a targeted assay where the panel is part of the identity.
- **An obs row is a 55 um spot on a fixed grid**, not a segmented cell. So there
  is no cell or nucleus area, `segmentation_method` is `grid`, and the array
  coordinates are worth keeping — they are the grid position, not a measurement.

Sources are fetched rather than assumed present, because a Visium study is
usually split across hosts: counts and full-resolution image in one place, the
spatial directory (`tissue_positions_list`, `scalefactors_json`) in another. The
spec gives a URL template for each.

The micron frame is derived, not published. `scalefactors_json.json` gives
`spot_diameter_fullres` — the spot's diameter in full-resolution pixels — and a
Visium spot is 55 um across by construction, so

    pixel_size_um = 55.0 / spot_diameter_fullres

and micron coordinates follow from the full-resolution pixel columns. That is
the same relation the atlas records for these sections.

Run:
    python scripts/build_visium_package.py --spec specs/<dataset>.json \\
        [--samples ...] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.request

import h5py
import numpy as np
import pandas as pd
import tifffile

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
UA = {"User-Agent": "somics/0.1 (mailto:aoliveirapisco@chanzuckerberg.com)"}

# tissue_positions_list has no header in the Space Ranger 1.x layout LIBD used.
POSITION_COLUMNS = [
    "barcode",
    "in_tissue",
    "array_row",
    "array_col",
    "pxl_row_in_fullres",
    "pxl_col_in_fullres",
]


def fetch(url: str, dest: str) -> str:
    """Download once. Sources are large and re-runs are expected."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 22):
            out.write(chunk)
    os.replace(tmp, dest)
    return dest


def read_h5(path: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """var table, barcodes, per-spot total counts, per-spot detected genes.

    The matrix is CSC over (features x spots), which is CSR over (spots x
    features) read as the transpose — the same three arrays. So both per-spot
    summaries are slices of `indptr` and never materialize the matrix.
    """
    with h5py.File(path, "r") as handle:
        group = handle["matrix"]
        features = group["features"]
        var = pd.DataFrame(
            {
                "gene_id": np.asarray(features["id"]).astype(str),
                "gene_name": np.asarray(features["name"]).astype(str),
                "feature_type": np.asarray(features["feature_type"]).astype(str),
                "genome": np.asarray(features["genome"]).astype(str),
            }
        )
        barcodes = np.asarray(group["barcodes"]).astype(str)
        indptr = np.asarray(group["indptr"])
        data = np.asarray(group["data"])
    n_genes = np.diff(indptr).astype(np.int32)
    totals = np.add.reduceat(np.concatenate([data, [0]]), indptr[:-1]).astype(np.float64)
    totals[np.diff(indptr) == 0] = 0
    return var, barcodes, totals, n_genes


def build_sample(sample: str, spec: dict, source: str, out_dir: str) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    entry = spec["samples"][sample]

    counts = fetch(
        spec["counts_url"].format(sample=sample),
        os.path.join(source, sample, "filtered_feature_bc_matrix.h5"),
    )
    image = fetch(
        spec["image_url"].format(sample=sample), os.path.join(source, sample, "full_image.tif")
    )
    for name in spec["spatial_files"]:
        fetch(
            spec["spatial_url"].format(sample=sample, file=name), os.path.join(source, sample, name)
        )

    scale = json.load(open(os.path.join(source, sample, "scalefactors_json.json")))
    diameter = float(scale["spot_diameter_fullres"])
    unit = float(spec["unit_size_um"])
    pixel_size = unit / diameter

    positions = pd.read_csv(
        os.path.join(source, sample, "tissue_positions_list.txt"),
        header=None,
        names=POSITION_COLUMNS,
    )
    positions["barcode"] = positions["barcode"].astype(str)

    var, barcodes, totals, n_genes = read_h5(counts)
    # The positions file lists every spot on the slide; the filtered matrix only
    # those under tissue. Align to the matrix, which is what ingestion streams.
    positions = positions.set_index("barcode").reindex(barcodes)
    missing = int(positions["pxl_row_in_fullres"].isna().sum())
    if missing:
        raise ValueError(
            f"{sample}: {missing} matrix barcode(s) absent from tissue_positions_list; "
            f"obs and matrix rows would not correspond"
        )

    x_px = positions["pxl_col_in_fullres"].to_numpy(dtype=float)
    y_px = positions["pxl_row_in_fullres"].to_numpy(dtype=float)
    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(barcodes), dtype=np.int64),
            "source_obs_id": barcodes,
            "x_um": x_px * pixel_size,
            "y_um": y_px * pixel_size,
            "x_px": x_px,
            "y_px": y_px,
            "pixel_size_um": pixel_size,
            "unit_size_um": unit,
            "n_counts": totals,
            "n_genes": n_genes,
            "in_tissue": positions["in_tissue"].to_numpy().astype(bool),
            "section_id": entry["section_id"],
            "donor_id": entry["donor_id"],
        }
    )
    obs["source_extras_json"] = [
        json.dumps(
            {
                "array_row": int(r),
                "array_col": int(c),
                "position_um": entry["position_um"],
                "replicate": entry["replicate"],
            }
        )
        for r, c in zip(positions["array_row"], positions["array_col"], strict=True)
    ]

    obs_path = os.path.join(out_dir, f"{sample}_obs.csv")
    obs.to_csv(obs_path, index=False)
    print(
        f"  wrote {os.path.basename(obs_path)}: {len(obs)} spots, "
        f"{int(obs.in_tissue.sum())} under tissue"
    )

    var_path = os.path.join(out_dir, f"{sample}_var.csv")
    var.to_csv(var_path, index=False)
    print(f"  wrote {os.path.basename(var_path)}: {len(var)} features")

    # Hardlink rather than copy: the full-resolution image is ~0.5 GB a section
    # and the collection's coalesce moves files out of staging anyway, so a copy
    # would double the study on disk for no gain. Fall back to a copy if the
    # download cache and the package are not on one filesystem.
    for src, name in ((counts, "filtered_feature_bc_matrix.h5"), (image, f"{sample}_he_image.tif")):
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest):
            continue
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)

    with tifffile.TiffFile(image) as tif:
        height, width = (int(d) for d in tif.series[0].levels[0].shape[:2])

    return {
        "sample": sample,
        "n_spots": int(len(obs)),
        "n_features": int(len(var)),
        "pixel_size_um": pixel_size,
        "spot_diameter_fullres": diameter,
        "height_px": height,
        "width_px": width,
        "section_id": entry["section_id"],
        "donor_id": entry["donor_id"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--samples", nargs="*")
    parser.add_argument("--source")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")

    samples = args.samples or list(spec["samples"])
    unknown = [s for s in samples if s not in spec["samples"]]
    if unknown:
        raise SystemExit(f"not in {args.spec}: {unknown}")

    os.makedirs(out, exist_ok=True)
    summary = [build_sample(s, spec, source, os.path.join(out, s)) for s in samples]
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    print(f"\n{len(summary)} sample(s), {sum(e['n_spots'] for e in summary)} spots")


if __name__ == "__main__":
    main()
