#!/usr/bin/env python3
"""Per-FOV obs/var/matrix and a DAPI image for a HuBMAP seqFISH dataset, spec-driven.

Source layout (Cai lab, HuBMAP): ``data/count_matrix_pos<N>.csv`` is genes x cells
with ``cell_<id>`` columns and three ``blank`` rows; ``data/cell_centroids.csv`` has
FieldID, CellID, regionID, X, Y, Z, Volume with X/Y in FOV pixels (0-2048 at
0.112 um/px); ``segmentation_mask/dapi-hyb0-pos<N>.tif`` is a DAPI z-stack.

Each FOV becomes one section (see ``make_seqfish_specs.py`` for why) with the
same outputs as the MERFISH/Xenium builders -- a 10x-format
``cell_feature_matrix.h5``, ``<sample>_obs.csv``, ``cell_feature_matrix_var.csv``
-- plus ``morphology_focus.ome.tif``, the DAPI stack max-projected, in the pixel
frame the centroids use. The assembler and runner are the MERFISH ones
(``SOMICS_BUILD_SCRIPT=scripts/build_seqfish_package.py``).

Run:
    python scripts/build_seqfish_package.py --spec specs/seqfish/<dataset>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import scipy.sparse as sp
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_merfish_package import BLANK_FEATURE_TYPE, GENE_FEATURE_TYPE, write_10x_h5  # noqa: E402

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def sources_for(spec: dict) -> list[tuple[str, str]]:
    return [(f["url"], f["dest"]) for f in spec["source"]["files"]]


def read_counts(path: str) -> tuple[sp.csr_matrix, np.ndarray, pd.DataFrame]:
    """(cells x features) counts, cell ids, and a var table in the 10x vocabulary."""
    cm = pd.read_csv(path)
    cm.iloc[:, 0] = cm.iloc[:, 0].astype(str).str.strip()
    # The export repeats a gene row verbatim now and then (EEF2 on the small
    # bowel matrices, identical counts): one feature, not two. A repeated name
    # with *different* counts would be two probes and is kept, suffixed.
    blank_rows = cm.iloc[:, 0].str.lower() == "blank"
    cm = pd.concat([cm[~blank_rows].drop_duplicates(), cm[blank_rows]])  # the blank barcodes stay, all of them
    names = cm.iloc[:, 0].to_numpy().astype(str)
    seen: dict[str, int] = {}
    genes = []
    for g in names:
        if g.lower() != "blank" and g in seen:
            seen[g] += 1
            genes.append(f"{g}__{seen[g]}")
        else:
            seen[g] = 1
            genes.append(g)
    genes = np.array(genes)
    cells = np.array([c.replace("cell_", "") for c in cm.columns[1:]])
    counts = np.round(cm.iloc[:, 1:].to_numpy(dtype=float)).astype(np.int64).T
    is_blank = np.array([g.lower() == "blank" for g in genes])
    ids = genes.copy()
    ids[is_blank] = [f"blank_{i + 1}" for i in range(int(is_blank.sum()))]
    var = pd.DataFrame(
        {
            "gene_id": ids,  # symbols; the lab publishes no Ensembl ids
            "gene_name": ids,
            "feature_type": np.where(is_blank, BLANK_FEATURE_TYPE, GENE_FEATURE_TYPE),
            "genome": "GRCh38",
        }
    )
    return sp.csr_matrix(counts), cells, var


def max_projection(path: str, out: str) -> tuple[int, int]:
    """Collapse the DAPI z-stack to one plane; returns (height, width)."""
    # The Cai lab stacks are ImageJ-style: one valid IFD describes the first
    # plane, the remaining planes follow as contiguous raw pixels, and the
    # next-IFD pointer is bogus ("invalid page offset"). tifffile's page
    # iteration yields (0, 0) frames after the first and its ImageJ series
    # parser raises "incompatible keyframe", so the planes are read directly:
    # from the first plane's data offset, as many whole planes as the file holds.
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        height, width = (int(d) for d in page.shape[:2])
        # ImageJ writes big-endian; honour the file's byte order or every
        # value is byte-swapped (means ~33000 on a stack whose max is 6270).
        dtype = np.dtype(page.dtype).newbyteorder(tif.byteorder)
        offset = int(page.dataoffsets[0])
        contiguous = len(page.dataoffsets) == 1 and page.compression == 1
    plane_bytes = height * width * dtype.itemsize
    n_planes = (os.path.getsize(path) - offset) // plane_bytes
    if not contiguous or n_planes < 1:
        raise ValueError(f"{path}: not a contiguous uncompressed stack (offsets {len(page.dataoffsets)}, compression {page.compression})")
    stack = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(int(n_planes), height, width))
    proj = np.asarray(stack.max(axis=0))
    del stack
    if proj.ndim != 2 or min(proj.shape) == 0:
        raise ValueError(f"{path}: unexpected DAPI plane shape {proj.shape}")
    print(f"    DAPI stack: {n_planes} planes of {height}x{width} {dtype} read contiguously from byte {offset}")
    tifffile.imwrite(out + ".part", proj, tile=(512, 512), compression="zlib")
    os.replace(out + ".part", out)
    return int(proj.shape[0]), int(proj.shape[1])


def build_fov(sample: str, entry: dict, spec: dict, src: str, centroids: pd.DataFrame, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    n = int(entry["fov"])
    px = float(spec["source"]["pixel_size_um"])
    matrix, cells, var = read_counts(os.path.join(src, f"pos{n}", "count_matrix.csv"))
    cen = centroids[centroids.FieldID == n].copy()
    cen["CellID"] = cen["CellID"].astype(int).astype(str)
    cen = cen.set_index("CellID")
    missing = [c for c in cells if c not in cen.index]
    if missing:
        raise ValueError(f"{sample}: {len(missing)} matrix cells have no centroid, e.g. {missing[:5]}")
    cen = cen.loc[cells]
    height, width = max_projection(os.path.join(src, f"pos{n}", "dapi.tif"), os.path.join(out_dir, "morphology_focus.ome.tif"))
    x_px, y_px = cen.X.to_numpy(dtype=float), cen.Y.to_numpy(dtype=float)
    if x_px.max() >= width or y_px.max() >= height or x_px.min() < 0 or y_px.min() < 0:
        raise ValueError(f"{sample}: centroids ({x_px.min():.0f}..{x_px.max():.0f}, {y_px.min():.0f}..{y_px.max():.0f}) fall outside the {height}x{width} DAPI plane")
    is_gene = (var.feature_type == GENE_FEATURE_TYPE).to_numpy()
    n_counts = np.asarray(matrix.sum(axis=1)).ravel()
    ids = np.array([f"{sample}:{c}" for c in cells], dtype=object)
    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(cells), dtype=np.int64),
            "source_obs_id": ids,
            "x_um": x_px * px,
            "y_um": y_px * px,
            "x_px": x_px,
            "y_px": y_px,
            "pixel_size_um": px,
            "n_counts": n_counts,
            "n_genes": np.asarray((matrix[:, is_gene] > 0).sum(axis=1)).ravel(),
            "negative_control_counts": np.asarray(matrix[:, ~is_gene].sum(axis=1)).ravel(),
            "unassigned_counts": 0,
            "anatomical_region": entry.get("anatomical_region"),
            "section_id": entry["section_id"],
            "donor_id": entry["donor_id"],
            "panel_name": spec["panel"]["panel_name"],
            "source_extras_json": [
                json.dumps({"fov": n, "z_plane": float(z), "volume_vox": float(v), "region_id": int(r)})
                for z, v, r in zip(cen.Z, cen.Volume, cen.regionID, strict=True)
            ],
        }
    )
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    write_10x_h5(os.path.join(out_dir, "cell_feature_matrix.h5"), matrix, ids, var, "GRCh38")
    var.to_csv(os.path.join(out_dir, "cell_feature_matrix_var.csv"), index=False)
    print(f"  {sample}: {len(cells)} cells, {int(is_gene.sum())} genes + {int((~is_gene).sum())} blanks, median {np.median(n_counts):.0f} counts, DAPI {height}x{width}")
    return {
        "sample": sample,
        "section_id": entry["section_id"],
        "donor_id": entry["donor_id"],
        "donor": {"donor_id": entry["donor_id"], "sex": spec["donors"][entry["donor_id"]].get("sex", "unknown"), "genotype": None},
        "image_file": "morphology_focus.ome.tif",
        "image_description": "DAPI z-stack of the field of view, max-projected to one plane; the cell centroids index this frame directly.",
        "pixel_size_um": px,
        "height_px": height,
        "width_px": width,
        "n_cells": int(len(cells)),
        "n_genes_panel": int(is_gene.sum()),
        "n_features": int(len(var)),
        "median_transcripts_per_cell": float(np.median(n_counts)),
        "spatial_unit": "cell",
        "segmentation_method": spec["segmentation_method"],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--source")
    ap.add_argument("--out")
    ap.add_argument("--list-sources", action="store_true")
    args = ap.parse_args(argv)
    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")
    if args.list_sources:
        for url, rel in sources_for(spec):
            print(f"{url}\t{os.path.join(source, rel)}")
        return
    os.makedirs(out, exist_ok=True)
    centroids = pd.read_csv(os.path.join(source, "cell_centroids.csv"))
    geometry = [
        build_fov(sample, entry, spec, source, centroids, os.path.join(out, sample))
        for sample, entry in spec["samples"].items()
    ]
    json.dump(geometry, open(os.path.join(out, "sample_geometry.json"), "w"), indent=2)
    print(f"\n{len(geometry)} FOV section(s), {sum(g['n_cells'] for g in geometry)} cells")


if __name__ == "__main__":
    main()
