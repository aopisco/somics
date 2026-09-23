#!/usr/bin/env python3
"""Per-section obs/var/matrix (and optionally an image) for Stereo-seq data, spec-driven.

Stereo-seq (BGI/STOmics) captures on a DNB array with a 500 nm pitch (220 nm
spots); the atlas unit is a **square bin of DNBs**, the Visium HD shape:
``spatial_unit = bin``, ``unit_size_um = bin_dnb * 0.5``. The default bin is 20
DNBs = 10 um, near a cell, as Visium HD is ingested at 8 um. Cell-segmented
exports (cellbin) are ingested as cells.

Source layouts, one per ``source.layout`` (per sample, so a spec can mix):

  gem        a GEM table: columns geneID x y MIDCount [ExonCount] with optional
             ``#`` header lines (``#OffsetX=``, ``#OffsetY=``), plain or gzipped --
             sniffed by magic bytes, because MOSTA publishes plain text under a
             ``.tsv.gz`` name and gzip under the same name for other sections.
             Aggregated to bins here.
  gef_bin    a GEF (HDF5): ``geneExp/bin1/{expression,gene}``; aggregated to bins.
  gef_cellbin a cellbin GEF: ``cellBin/{cell,cellExp,gene}``; cells with centroids
             and areas as the lab segmented them.
  h5ad_bins  an AnnData of bins with ``obsm['spatial']`` in DNB coordinates (or a
             bin index * pitch), counts in X; ``bin_dnb`` from the spec.

A sample may carry an ``image`` (registered ssDNA/H&E TIFF, plain or gzipped,
1 px = 1 DNB): it becomes the section image, and every bin gets ``x_px``/``y_px``
in that frame. Without one the section is expression-only.

Sources may be single files (fetched by URL) or members of an archive already
staged in S3 (a GEO ``_RAW.tar``, a zip): ``files`` entries with ``member`` are
extracted from ``dest`` at build time.

Outputs mirror the MERFISH builder so the MERFISH assembler, harmonizer and
runner apply: 10x-format ``cell_feature_matrix.h5`` (features in 10x's
vocabulary), ``<sample>_obs.csv``, ``cell_feature_matrix_var.csv``, and
``morphology_focus.ome.tif`` when there is an image. Stereo-CITE ADT tables
become ``<sample>_protein_{intensity,obs,var}.csv`` (a second feature space).

Run:
    python scripts/build_stereoseq_package.py --spec specs/stereoseq/<dataset>.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import tarfile
import zipfile

import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_merfish_package import GENE_FEATURE_TYPE, section_key, write_10x_h5  # noqa: E402

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
DNB_PITCH_UM = 0.5
DEFAULT_BIN_DNB = 20
GEM_COLUMNS = {"geneid": "gene", "gene": "gene", "x": "x", "y": "y", "midcounts": "count", "midcount": "count", "umicount": "count", "counts": "count", "count": "count"}


def sources_for(spec: dict) -> list[tuple[str, str]]:
    return [(f["url"], f["dest"]) for f in spec["source"]["files"]]


# ---------------------------------------------------------------------------
# file helpers
# ---------------------------------------------------------------------------


def is_gzip(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(2) == b"\x1f\x8b"


def open_text(path: str):
    return gzip.open(path, "rt") if is_gzip(path) else open(path, "rt")


def ensure_member(src: str, entry: dict) -> str:
    """Path to a sample's file: extracted from an archive when the spec says so."""
    path = os.path.join(src, entry["dest"])
    member = entry.get("member")
    if not member:
        return path
    out = os.path.join(src, "_members", os.path.basename(member))
    if os.path.exists(out):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z, z.open(member) as f, open(out + ".part", "wb") as o:
            while chunk := f.read(1 << 24):
                o.write(chunk)
    else:
        with tarfile.open(path) as t:
            f = t.extractfile(member)
            if f is None:
                raise FileNotFoundError(f"{member} not in {path}")
            with open(out + ".part", "wb") as o:
                while chunk := f.read(1 << 24):
                    o.write(chunk)
    os.replace(out + ".part", out)
    return out


def read_image(path: str) -> np.ndarray:
    import tifffile

    if is_gzip(path):
        with gzip.open(path, "rb") as f:
            data = f.read()
        import io

        return tifffile.imread(io.BytesIO(data))
    return tifffile.imread(path)


# ---------------------------------------------------------------------------
# readers -> (genes, x, y, count) at DNB resolution, or cells
# ---------------------------------------------------------------------------


def read_gem(path: str) -> tuple[pd.DataFrame, dict]:
    """GEM table -> long frame (gene, x, y, count) with header offsets applied."""
    meta: dict = {}
    n_meta = 0
    with open_text(path) as handle:
        while True:
            line = handle.readline()
            fields = [c.strip().lstrip("#") for c in line.rstrip("\n").split("\t")]
            if len(fields) >= 3 and all(f.lower() in GEM_COLUMNS for f in fields[:3]):
                header = fields  # the column line, whether or not it starts with '#'
                break
            if not line.startswith("#"):
                raise ValueError(f"{path}: no GEM header found; first non-comment line is {line[:80]!r}")
            k, _, v = line[1:].strip().partition("=")
            meta[k] = v
            n_meta += 1
    cols = header
    rename = {c: GEM_COLUMNS[c.lower()] for c in cols if c.lower() in GEM_COLUMNS}
    use = [c for c in cols if c in rename]
    dtypes = {c: ("category" if rename[c] == "gene" else np.int64) for c in use}
    # gene as a categorical: a 100M-row embryo GEM as Python strings is >6 GB,
    # as category codes it is <1 GB.
    try:
        frame = pd.read_csv(open_text(path), sep="\t", names=cols, skiprows=n_meta + 1, usecols=use, dtype=dtypes).rename(columns=rename)
    except (ValueError, TypeError):
        # A header line repeated inside the file (E15.5_E2S1: 'x' where an int
        # belongs) or another stray text row: read as text, drop rows whose
        # coordinates are not integers, then cast.
        frame = pd.read_csv(open_text(path), sep="\t", names=cols, skiprows=n_meta + 1, usecols=use, dtype=str).rename(columns=rename)
        ok = frame["x"].str.fullmatch(r"-?\d+") & frame["y"].str.fullmatch(r"-?\d+") & frame["count"].str.fullmatch(r"\d+")
        dropped = int((~ok).sum())
        print(f"    {dropped} non-numeric row(s) dropped from {os.path.basename(path)} (repeated header or stray text)")
        frame = frame[ok]
        frame = frame.astype({"x": np.int64, "y": np.int64, "count": np.int64})
        frame["gene"] = frame["gene"].astype("category")
    if "count" not in frame.columns:
        raise ValueError(f"{path}: no count column among {cols}")
    off_x, off_y = int(float(meta.get("OffsetX", 0))), int(float(meta.get("OffsetY", 0)))
    frame["x"] += off_x
    frame["y"] += off_y
    return frame[["gene", "x", "y", "count"]], meta


def read_gef_bin1(path: str) -> tuple[pd.DataFrame, dict]:
    with h5py.File(path, "r") as f:
        grp = f["geneExp"]["bin1"]
        expr = grp["expression"][:]
        genes = grp["gene"][:]
        attrs = {k: (v.item() if hasattr(v, "item") else v) for k, v in grp["expression"].attrs.items()}
    gene_field = "geneID" if "geneID" in genes.dtype.names else ("gene" if "gene" in genes.dtype.names else genes.dtype.names[0])
    names = np.array([g.decode() if isinstance(g, bytes) else str(g) for g in genes[gene_field]])
    counts_per_gene = genes["count"].astype(np.int64)
    gene_idx = np.repeat(np.arange(len(names)), counts_per_gene)
    frame = pd.DataFrame({"gene": names[gene_idx], "x": expr["x"].astype(np.int64), "y": expr["y"].astype(np.int64), "count": expr["count"].astype(np.int64)})
    off_x, off_y = int(attrs.get("minX", 0)), int(attrs.get("minY", 0))
    # GEF stores x/y relative to minX/minY in some SAW versions and absolute in
    # others; the range tells which (relative starts at 0).
    if frame.x.min() == 0 and off_x:
        frame["x"] += off_x
        frame["y"] += off_y
    return frame, attrs


def bin_long_frame(frame: pd.DataFrame, bin_dnb: int) -> tuple[sp.csr_matrix, pd.DataFrame, pd.DataFrame]:
    """Aggregate DNB-level counts to bins. Returns (bins x genes CSR, bins, var)."""
    bx = (frame.x.to_numpy() // bin_dnb).astype(np.int64)
    by = (frame.y.to_numpy() // bin_dnb).astype(np.int64)
    key = bx * (1 << 32) + by
    bin_ids, bin_inverse = np.unique(key, return_inverse=True)
    if isinstance(frame.gene.dtype, pd.CategoricalDtype):
        cats = frame.gene.cat
        genes, gene_inverse = np.asarray(cats.categories, dtype=object), cats.codes.to_numpy().astype(np.int64)
        used = np.unique(gene_inverse)
        if len(used) != len(genes):  # drop categories no row uses
            remap = -np.ones(len(genes), dtype=np.int64); remap[used] = np.arange(len(used)); genes, gene_inverse = genes[used], remap[gene_inverse]
    else:
        genes, gene_inverse = np.unique(frame.gene.to_numpy(), return_inverse=True)
    matrix = sp.csr_matrix(
        (frame["count"].to_numpy().astype(np.int64), (bin_inverse.ravel(), gene_inverse.ravel())),
        shape=(len(bin_ids), len(genes)),
    )
    matrix.sum_duplicates()
    bins = pd.DataFrame({"bin_x": bin_ids >> 32, "bin_y": bin_ids & ((1 << 32) - 1)})
    var = pd.DataFrame({"gene_id": genes, "gene_name": genes, "feature_type": GENE_FEATURE_TYPE})
    return matrix, bins, var


def read_gef_cellbin(path: str) -> tuple[sp.csr_matrix, pd.DataFrame, pd.DataFrame]:
    with h5py.File(path, "r") as f:
        cb = f["cellBin"]
        cells = cb["cell"][:]
        cell_exp = cb["cellExp"][:]
        genes = cb["gene"][:]
    gene_field = next(n for n in ("geneName", "gene", "geneID") if n in genes.dtype.names)
    names = np.array([g.decode() if isinstance(g, bytes) else str(g) for g in genes[gene_field]])
    n_cells = len(cells)
    per_cell = cells["geneCount"].astype(np.int64)
    rows = np.repeat(np.arange(n_cells), per_cell)
    cols = cell_exp["geneID"].astype(np.int64)
    matrix = sp.csr_matrix((cell_exp["count"].astype(np.int64), (rows, cols)), shape=(n_cells, len(names)))
    matrix.sum_duplicates()
    meta = pd.DataFrame({"cell_id": cells["id"].astype(np.int64), "x": cells["x"].astype(float), "y": cells["y"].astype(float)})
    if "area" in cells.dtype.names:
        meta["area_dnb2"] = cells["area"].astype(float)
    var = pd.DataFrame({"gene_id": names, "gene_name": names, "feature_type": GENE_FEATURE_TYPE})
    return matrix, meta, var


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build_sample(sample: str, entry: dict, spec: dict, src: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    layout = entry.get("layout", spec["source"].get("layout", "gem"))
    pitch = float(spec["source"].get("dnb_pitch_um", DNB_PITCH_UM))
    bin_dnb = int(entry.get("bin_dnb", spec["source"].get("bin_dnb", DEFAULT_BIN_DNB)))
    files = {f["role"]: f for f in spec["source"]["files"] if f.get("sample") == sample}
    protein = None
    extras: dict = {}

    if layout in ("gem", "gef_bin"):
        path = ensure_member(src, files["counts"])
        frame, meta = read_gem(path) if layout == "gem" else read_gef_bin1(path)
        extras["source_header"] = {k: str(v) for k, v in meta.items()} if meta else {}
        n_dnb_rows = int(len(frame))
        matrix, bins, var = bin_long_frame(frame, bin_dnb)
        del frame
        x_dnb = (bins.bin_x.to_numpy() + 0.5) * bin_dnb  # bin centre, DNB units
        y_dnb = (bins.bin_y.to_numpy() + 0.5) * bin_dnb
        ids = np.array([f"{sample}:bin{bin_dnb}_{bx}_{by}" for bx, by in zip(bins.bin_x, bins.bin_y)], dtype=object)
        unit = {"spatial_unit": "bin", "segmentation_method": "grid", "unit_size_um": bin_dnb * pitch}
        area = np.full(len(ids), (bin_dnb * pitch) ** 2)
        print(f"  {sample}: {n_dnb_rows:,} DNB-gene rows -> {len(ids):,} bins of {bin_dnb} DNB ({bin_dnb * pitch:g} um), {matrix.shape[1]:,} genes")
    elif layout == "gef_cellbin":
        path = ensure_member(src, files["counts"])
        matrix, meta, var = read_gef_cellbin(path)
        x_dnb, y_dnb = meta.x.to_numpy(), meta.y.to_numpy()
        ids = np.array([f"{sample}:cell_{c}" for c in meta.cell_id], dtype=object)
        unit = {"spatial_unit": "cell", "segmentation_method": entry.get("segmentation_method", spec.get("segmentation_method", "other")), "unit_size_um": None}
        area = meta["area_dnb2"].to_numpy() * pitch**2 if "area_dnb2" in meta.columns else np.full(len(ids), np.nan)
        print(f"  {sample}: {len(ids):,} cells (cellbin), {matrix.shape[1]:,} genes")
    elif layout == "h5ad_bins":
        import anndata as ad

        path = ensure_member(src, files["counts"])
        adata = ad.read_h5ad(path)
        adata.var_names_make_unique()
        xy = np.asarray(adata.obsm[entry.get("spatial_key", "spatial")], dtype=float)
        coord_unit = entry.get("coord_unit", "dnb")  # dnb | bin (index * bin_dnb)
        x_dnb, y_dnb = (xy[:, 0], xy[:, 1]) if coord_unit == "dnb" else (xy[:, 0] * bin_dnb + bin_dnb / 2, xy[:, 1] * bin_dnb + bin_dnb / 2)
        layer = entry.get("counts_layer")
        X = adata.layers[layer] if layer else adata.X
        matrix = sp.csr_matrix(X)
        if not np.allclose(matrix.data, np.round(matrix.data)):
            raise ValueError(f"{sample}: counts are not integers; set counts_layer to the raw layer")
        matrix.data = np.round(matrix.data).astype(np.int64)
        var = pd.DataFrame({"gene_id": adata.var_names.astype(str), "gene_name": adata.var_names.astype(str), "feature_type": GENE_FEATURE_TYPE})
        if "gene_ids" in adata.var.columns:
            var["gene_id"] = adata.var["gene_ids"].astype(str).to_numpy()
        ids = np.array([f"{sample}:{o}" for o in adata.obs_names], dtype=object)
        unit = {"spatial_unit": "bin", "segmentation_method": "grid", "unit_size_um": bin_dnb * pitch}
        area = np.full(len(ids), (bin_dnb * pitch) ** 2)
        if "protein" in files:  # Stereo-CITE ADT table aligned on obs names
            prot = ad.read_h5ad(ensure_member(src, files["protein"]))
            prot = prot[adata.obs_names]
            P = sp.csr_matrix(prot.X)
            protein = {"targets": [str(t) for t in prot.var_names], "dense": np.asarray(P.todense())}
        print(f"  {sample}: {len(ids):,} bins from h5ad ({bin_dnb} DNB = {bin_dnb * pitch:g} um), {matrix.shape[1]:,} genes" + (f", {len(protein['targets'])} proteins" if protein else ""))
    else:
        raise ValueError(f"{sample}: unknown layout {layout!r}")

    # image: registered stain at DNB resolution
    image_file = None
    height = width = None
    x_px = y_px = None
    if "image" in files:
        import tifffile

        img = read_image(ensure_member(src, files["image"]))
        if img.ndim == 3 and img.shape[0] in (1, 2, 3, 4) and img.shape[1] > 16:
            img = np.moveaxis(img, 0, -1)
        height, width = int(img.shape[0]), int(img.shape[1])
        img_scale = float(entry.get("image_px_per_dnb", 1.0))
        x_px, y_px = x_dnb * img_scale, y_dnb * img_scale
        inside = (x_px >= 0) & (x_px < width) & (y_px >= 0) & (y_px < height)
        if inside.mean() < 0.95:
            raise ValueError(f"{sample}: only {inside.mean():.1%} of units fall inside the {height}x{width} image; image_px_per_dnb or the coordinate origin is wrong")
        image_file = "morphology_focus.ome.tif"
        tifffile.imwrite(os.path.join(out_dir, image_file + ".part"), img, tile=(1024, 1024), bigtiff=True, compression="zlib")
        os.replace(os.path.join(out_dir, image_file + ".part"), os.path.join(out_dir, image_file))
        extras["image_px_per_dnb"] = img_scale

    if var.gene_id.duplicated().any():
        # The cellbin GEF gene table repeats a symbol (two entries, same name);
        # the harmonizer's keyed merge needs unique feature ids, so the columns
        # are summed per unique symbol.
        names, inv = np.unique(var.gene_id.to_numpy(), return_inverse=True)
        collapse = sp.csr_matrix((np.ones(len(inv)), (np.arange(len(inv)), inv.ravel())), shape=(len(inv), len(names)))
        matrix = sp.csr_matrix(matrix @ collapse)
        print(f"    {len(inv) - len(names)} duplicated gene name(s) summed into one column each")
        var = pd.DataFrame({"gene_id": names, "gene_name": names, "feature_type": GENE_FEATURE_TYPE})

    n_counts = np.asarray(matrix.sum(axis=1)).ravel()
    n_genes = np.asarray((matrix > 0).sum(axis=1)).ravel()
    obs = pd.DataFrame(
        {
            **({"barcode": ids} if protein else {}),
            "obs_index": np.arange(len(ids), dtype=np.int64),
            "source_obs_id": ids,
            "x_um": x_dnb * pitch,
            "y_um": y_dnb * pitch,
            **({"x_px": x_px, "y_px": y_px, "pixel_size_um": pitch / float(entry.get("image_px_per_dnb", 1.0))} if image_file else {}),
            **({"unit_size_um": unit["unit_size_um"]} if unit["unit_size_um"] else {}),
            "n_counts": n_counts,
            "n_genes": n_genes,
            "negative_control_counts": 0,
            "unassigned_counts": 0,
            "cell_area_um2": area,
            "section_id": entry["section_id"],
            "donor_id": entry["donor_id"],
            "panel_name": spec["panel"]["panel_name"],
            "source_extras_json": json.dumps({"layout": layout, "bin_dnb": bin_dnb if unit["spatial_unit"] == "bin" else None, **extras}),
        }
    )
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    var["genome"] = spec["source"].get("genome", "unknown")
    write_10x_h5(os.path.join(out_dir, "cell_feature_matrix.h5"), matrix, ids, var, var.genome.iloc[0])
    var.to_csv(os.path.join(out_dir, "cell_feature_matrix_var.csv"), index=False)
    if protein:
        pd.DataFrame(protein["dense"], columns=protein["targets"]).to_csv(os.path.join(out_dir, f"{sample}_protein_intensity.csv"), index=False)
        pd.DataFrame({"barcode": ids, "obs_index": np.arange(len(ids)), "source_obs_id": ids}).to_csv(os.path.join(out_dir, f"{sample}_protein_obs.csv"), index=False)
        pd.DataFrame({"var_index": protein["targets"], "target_name": protein["targets"], "feature_id": protein["targets"], "is_stain": False, "is_control": [bool(re.search(r"isotype|control|blank|IgG", t, re.I)) for t in protein["targets"]]}).to_csv(os.path.join(out_dir, f"{sample}_protein_var.csv"), index=False)
    return {
        "sample": sample,
        "section_id": entry["section_id"],
        "donor_id": entry["donor_id"],
        "donor": {"donor_id": entry["donor_id"], "sex": spec["donors"].get(entry["donor_id"], {}).get("sex", "unknown"), "genotype": None},
        "image_file": image_file,
        "image_description": entry.get("image_description", "Registered nuclear (ssDNA) stain of the chip, 1 px = 1 DNB; bin centres index this frame.") if image_file else None,
        "pixel_size_um": (pitch / float(entry.get("image_px_per_dnb", 1.0))) if image_file else None,
        "height_px": height,
        "width_px": width,
        "n_cells": int(len(ids)),
        "n_genes_panel": int(matrix.shape[1]),
        "n_features": int(matrix.shape[1]),
        "median_transcripts_per_cell": float(np.median(n_counts)),
        "protein_targets": protein["targets"] if protein else None,
        **unit,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--source")
    ap.add_argument("--out")
    ap.add_argument("--samples", nargs="*")
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
    samples = args.samples or list(spec["samples"])
    geometry = [build_sample(s, spec["samples"][s], spec, source, os.path.join(out, section_key(s))) for s in samples]
    for g in geometry:
        g["sample"] = section_key(g["sample"])
    json.dump(geometry, open(os.path.join(out, "sample_geometry.json"), "w"), indent=2)
    print(f"\n{len(geometry)} section(s), {sum(g['n_cells'] for g in geometry):,} units")


if __name__ == "__main__":
    main()
