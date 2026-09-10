#!/usr/bin/env python3
"""Derive per-section obs/var/matrix files for a MERFISH release, spec-driven.

The first MERFISH family in the atlas is the Allen Brain Cell Atlas's public
releases (``s3://allen-brain-cell-atlas``, no auth): one AnnData of raw counts
for the whole release plus a ``cell_metadata.csv`` carrying every cell's section
label, donor and position in mm. Vizgen's own showcase buckets are gated behind
a data-release form, so the vendor ``cell_by_gene.csv`` / ``cell_metadata.csv``
layout is a second source layout (``source.layout = "merscope_outs"``) that the
same section-level outputs can be produced from once a bundle is staged.

What comes out per section mirrors the Xenium builder so the downstream steps
are shared: a 10x-format ``cell_feature_matrix.h5`` (CSC over features x cells,
``matrix/features`` with the 10x vocabulary so ``harmonize_xenium_package.py``
maps the feature types), ``<section>_obs.csv`` and ``cell_feature_matrix_var.csv``.
There is **no image**: Allen publishes no per-section DAPI mosaics, only
CCF-resampled volumes, so these sections are expression-only, which the schema
allows (``he_crop``/``morphology_crop`` default null). ``x_px``/``y_px`` and
``pixel_size_um`` stay null for the same reason.

Sections are not listed in the spec. A release's sections are whatever
``cell_metadata.csv`` says they are (59 on C57BL6J-638850), so the builder
derives them at build time and records each in ``sample_geometry.json``; the
assembler and harmonizer read that. ``section_id`` is the release's own
section label (``C57BL6J-638850.37``), which is stable across rebuilds.

Run:
    python scripts/build_merfish_package.py --spec specs/merfish/<dataset>.json
    python scripts/build_merfish_package.py --spec ... --list-sources
"""

from __future__ import annotations

import argparse
import json
import os
import re

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

GENE_FEATURE_TYPE = "Gene Expression"
BLANK_FEATURE_TYPE = "Blank Codeword"  # the 10x label the Xenium harmonizer maps
BLANK_RE = re.compile(r"^blank", re.IGNORECASE)


def sources_for(spec: dict) -> list[tuple[str, str]]:
    """(url, relative destination) for every file the builder reads."""
    return [(f["url"], f["dest"]) for f in spec["source"]["files"]]


def unzip_flat(zip_path: str, out_dir: str) -> None:
    """Extract a zip's regular members into ``out_dir``, dropping the top-level
    folder and macOS resource forks."""
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir() or "__MACOSX" in info.filename or os.path.basename(info.filename).startswith("."):
                continue
            target = os.path.join(out_dir, os.path.basename(info.filename))
            if os.path.exists(target) and os.path.getsize(target) == info.file_size:
                continue
            with z.open(info) as src, open(target + ".part", "wb") as dst:
                while True:
                    chunk = src.read(1 << 24)
                    if not chunk:
                        break
                    dst.write(chunk)
            os.replace(target + ".part", target)


def section_key(label: str) -> str:
    """Directory-safe sample name for a section label."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(label))


# ---------------------------------------------------------------------------
# Allen Brain Cell Atlas layout
# ---------------------------------------------------------------------------


def read_allen_metadata(src: str, spec: dict) -> pd.DataFrame:
    path = os.path.join(src, "cell_metadata.csv")
    meta = pd.read_csv(path, dtype={"cell_label": str, "brain_section_label": str})
    release = spec["source"].get("feature_matrix_label")
    if release and "feature_matrix_label" in meta.columns:
        meta = meta[meta.feature_matrix_label == release]
    if "brain_section_label" not in meta.columns:
        raise ValueError(f"{path}: no brain_section_label column; columns are {list(meta.columns)}")
    return meta.set_index("cell_label", drop=False)


def gene_table(var: pd.DataFrame, src: str) -> pd.DataFrame:
    """Feature table in the 10x vocabulary: id (Ensembl), name (symbol), feature_type."""
    gene_csv = os.path.join(src, "gene.csv")
    symbol_by_id: dict[str, str] = {}
    if os.path.exists(gene_csv):
        genes = pd.read_csv(gene_csv, dtype=str)
        if {"gene_identifier", "gene_symbol"} <= set(genes.columns):
            symbol_by_id = dict(zip(genes.gene_identifier, genes.gene_symbol, strict=True))
    ids = np.array([str(i) for i in var.index])
    raw_names = var.gene_symbol.tolist() if "gene_symbol" in var.columns else [symbol_by_id.get(i) for i in ids]
    # A symbol can be missing (NaN on the HMBA releases, where the blanks and
    # some probes carry no symbol); the feature then keeps its own id as name.
    names = np.array(
        [n if isinstance(n, str) and n and n.lower() != "nan" else i for i, n in zip(ids, raw_names, strict=True)],
        dtype=object,
    )
    is_blank = np.array([bool(BLANK_RE.match(n)) or bool(BLANK_RE.match(i)) for i, n in zip(ids, names, strict=True)])
    return pd.DataFrame(
        {
            "gene_id": ids,
            "gene_name": names,
            "feature_type": np.where(is_blank, BLANK_FEATURE_TYPE, GENE_FEATURE_TYPE),
            "genome": spec_genome_placeholder(),
        }
    )


def spec_genome_placeholder() -> str:
    return "unknown"


def write_10x_h5(path: str, matrix: sp.csr_matrix, barcodes: np.ndarray, var: pd.DataFrame, genome: str) -> None:
    """Write (cells x features) counts as a 10x cell_feature_matrix.h5.

    10x stores CSC over (features x cells); CSC of A is CSR of A^T with the same
    arrays, so the CSR (cells x features) arrays are written verbatim with the
    transposed shape, which is exactly how ``somics.ingest.read_10x_h5_csr``
    reads them back.
    """
    matrix = sp.csr_matrix(matrix)
    matrix.sort_indices()
    n_cells, n_features = matrix.shape
    data = matrix.data
    if not np.issubdtype(data.dtype, np.integer):
        if not np.allclose(data, np.round(data)):
            raise ValueError(f"{path}: counts are not integers (raw layer expected)")
        data = np.round(data).astype(np.int32)
    tmp = path + ".part"
    with h5py.File(tmp, "w") as f:
        g = f.create_group("matrix")
        g.create_dataset("data", data=data.astype(np.int32), compression="gzip")
        g.create_dataset("indices", data=matrix.indices.astype(np.int64), compression="gzip")
        g.create_dataset("indptr", data=matrix.indptr.astype(np.int64))
        g.create_dataset("shape", data=np.array([n_features, n_cells], dtype=np.int32))
        g.create_dataset("barcodes", data=np.asarray(barcodes, dtype="S"))
        feat = g.create_group("features")
        feat.create_dataset("id", data=var.gene_id.to_numpy().astype("S"))
        feat.create_dataset("name", data=var.gene_name.to_numpy().astype("S"))
        feat.create_dataset("feature_type", data=var.feature_type.to_numpy().astype("S"))
        feat.create_dataset("genome", data=np.array([genome] * n_features, dtype="S"))
        feat.create_dataset("_all_tag_keys", data=np.array([b"genome"]))
    os.replace(tmp, path)


def build_allen(spec: dict, src: str, out: str) -> list[dict]:
    meta = read_allen_metadata(src, spec)
    h5ad = os.path.join(src, spec["source"].get("h5ad", "raw.h5ad"))
    adata = ad.read_h5ad(h5ad, backed="r")
    var = gene_table(adata.var, src)
    genome = spec["source"].get("genome", "unknown")
    var["genome"] = genome
    is_gene = (var.feature_type == GENE_FEATURE_TYPE).to_numpy()
    print(f"{spec['dataset_key']}: {adata.n_obs} cells x {adata.n_vars} features in {os.path.basename(h5ad)}; "
          f"{int(is_gene.sum())} genes, {int((~is_gene).sum())} blanks")

    obs_labels = pd.Index(adata.obs.index.astype(str))
    in_matrix = meta.index.isin(obs_labels)
    if not in_matrix.all():
        print(f"  {int((~in_matrix).sum())} metadata cells are not in the matrix; dropped")
        meta = meta[in_matrix]
    only = spec.get("only_sections")
    sections = sorted(meta.brain_section_label.unique(), key=str)
    if only:
        sections = [s for s in sections if s in set(only)]
    # Allen's mouse releases publish x/y/z in mm; the HMBA MERSCOPE releases
    # publish x_experiment/y_experiment in um. The spec names the columns and unit.
    xcol, ycol = spec["source"].get("xy_columns", ["x", "y"])
    scale = {"mm": 1000.0, "um": 1.0}[spec["source"].get("xy_unit", "mm")]
    donors_seen: dict[str, dict] = {}
    geometry = []
    for label in sections:
        cells = meta[meta.brain_section_label == label]
        sample = section_key(label)
        out_dir = os.path.join(out, sample)
        os.makedirs(out_dir, exist_ok=True)
        rows = obs_labels.get_indexer(cells.index)
        order = np.argsort(rows)
        rows, cells = rows[order], cells.iloc[order]
        sub = adata[rows].to_memory()
        matrix = sp.csr_matrix(sub.X)
        n_counts = np.asarray(matrix.sum(axis=1)).ravel()
        n_genes = np.asarray((matrix[:, is_gene] > 0).sum(axis=1)).ravel()
        blank_counts = np.asarray(matrix[:, ~is_gene].sum(axis=1)).ravel() if (~is_gene).any() else np.zeros(len(cells))

        donor_label = (
            str(cells.donor_label.iloc[0]) if "donor_label" in cells.columns
            else spec["source"].get("donor_label", spec["study"])
        )
        if donor_label not in donors_seen:
            donors_seen[donor_label] = {
                "donor_id": donor_label,
                "sex": _sex(cells.donor_sex.iloc[0]) if "donor_sex" in cells.columns else "unknown",
                "genotype": str(cells.donor_genotype.iloc[0]) if "donor_genotype" in cells.columns else None,
            }
        extras_cols = [
            c
            for c in (
                "cluster_alias", "average_correlation_score", "donor_genotype", "z",
                "subclass_confidence_score", "cluster_confidence_score", "high_quality_transfer",
                "brain_section_barcode", "segmentation_job_id", "qc_pass",
            )
            if c in cells.columns
        ]
        # Allen cell labels are long digit strings (39 digits on the Zhuang
        # releases): the CSV staging would type them as integers and mangle
        # them, so ids are namespaced with the section as Xenium 1.0 ids are.
        ids = np.array([f"{sample}:{c}" for c in cells.index], dtype=object)
        obs = pd.DataFrame(
            {
                "obs_index": np.arange(len(cells), dtype=np.int64),
                "source_obs_id": ids,
                "x_um": cells[xcol].to_numpy() * scale,
                "y_um": cells[ycol].to_numpy() * scale,
                "z_um": cells.z.to_numpy() * scale if "z" in cells.columns else np.nan,
                **({"passes_qc": cells.qc_pass.astype(bool).to_numpy()} if "qc_pass" in cells.columns else {}),
                "n_counts": n_counts,
                "n_genes": n_genes,
                "negative_control_counts": blank_counts,
                "unassigned_counts": 0,
                "section_id": str(label),
                "donor_id": donor_label,
                "panel_name": spec["panel"]["panel_name"],
                "source_extras_json": [json.dumps(r) for r in cells[extras_cols].to_dict(orient="records")],
            }
        )
        obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
        write_10x_h5(os.path.join(out_dir, "cell_feature_matrix.h5"), matrix, ids, var, genome)
        var.to_csv(os.path.join(out_dir, "cell_feature_matrix_var.csv"), index=False)
        geometry.append(
            {
                "sample": sample,
                "section_id": str(label),
                "donor_id": donor_label,
                "donor": donors_seen[donor_label],
                "image_file": None,
                "n_cells": int(len(cells)),
                "n_genes_panel": int(is_gene.sum()),
                "n_features": int(len(var)),
                "median_transcripts_per_cell": float(np.median(n_counts)),
                "z_mm": float(cells.z.iloc[0]) if "z" in cells.columns else None,
                "x_range_um": [float(obs.x_um.min()), float(obs.x_um.max())],
                "y_range_um": [float(obs.y_um.min()), float(obs.y_um.max())],
            }
        )
        print(f"  {label}: {len(cells)} cells, median {np.median(n_counts):.0f} counts")
    return geometry


def _sex(value) -> str:
    v = str(value).strip().lower()
    return {"m": "male", "male": "male", "f": "female", "female": "female"}.get(v, "unknown")


# ---------------------------------------------------------------------------
# Vizgen MERSCOPE outs layout (cell_by_gene.csv + cell_metadata.csv), one section
# ---------------------------------------------------------------------------


def build_merscope_outs(spec: dict, src: str, out: str) -> list[dict]:
    """A vendor bundle is one section: cell_by_gene.csv (cells x genes, first
    column the cell id) and cell_metadata.csv (fov, volume, center_x/y in um)."""
    section = spec["source"]["section_id"]
    sample = section_key(section)
    out_dir = os.path.join(out, sample)
    os.makedirs(out_dir, exist_ok=True)
    cbg = pd.read_csv(os.path.join(src, "cell_by_gene.csv"), index_col=0)
    meta = pd.read_csv(os.path.join(src, "cell_metadata.csv"), index_col=0)
    cbg.index = cbg.index.astype(str)
    meta.index = meta.index.astype(str)
    meta = meta.loc[cbg.index]
    names = cbg.columns.astype(str)
    is_blank = np.array([bool(BLANK_RE.match(n)) for n in names])
    var = pd.DataFrame(
        {
            "gene_id": names,  # Vizgen publishes symbols only; feature_id is the symbol
            "gene_name": names,
            "feature_type": np.where(is_blank, BLANK_FEATURE_TYPE, GENE_FEATURE_TYPE),
            "genome": spec["source"].get("genome", "unknown"),
        }
    )
    matrix = sp.csr_matrix(cbg.to_numpy())
    is_gene = ~is_blank
    n_counts = np.asarray(matrix.sum(axis=1)).ravel()
    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(meta), dtype=np.int64),
            "source_obs_id": f"{sample}:" + meta.index.to_numpy().astype(str),
            "x_um": meta.center_x.to_numpy(),
            "y_um": meta.center_y.to_numpy(),
            "n_counts": n_counts,
            "n_genes": np.asarray((matrix[:, is_gene] > 0).sum(axis=1)).ravel(),
            "negative_control_counts": np.asarray(matrix[:, is_blank].sum(axis=1)).ravel(),
            "unassigned_counts": 0,
            "cell_area_um2": meta.volume.to_numpy() if "volume" in meta.columns else np.nan,
            "section_id": section,
            "donor_id": spec["source"]["donor_id"],
            "panel_name": spec["panel"]["panel_name"],
            "source_extras_json": [json.dumps(r) for r in meta[[c for c in ("fov", "volume") if c in meta.columns]].to_dict(orient="records")],
        }
    )
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    write_10x_h5(os.path.join(out_dir, "cell_feature_matrix.h5"), matrix, obs.source_obs_id.to_numpy(), var, var.genome.iloc[0])
    var.to_csv(os.path.join(out_dir, "cell_feature_matrix_var.csv"), index=False)
    return [
        {
            "sample": sample,
            "section_id": section,
            "donor_id": spec["source"]["donor_id"],
            "donor": {"donor_id": spec["source"]["donor_id"], "sex": "unknown", "genotype": None},
            "image_file": None,
            "n_cells": int(len(meta)),
            "n_genes_panel": int(is_gene.sum()),
            "n_features": int(len(var)),
            "median_transcripts_per_cell": float(np.median(n_counts)),
        }
    ]


# ---------------------------------------------------------------------------
# Liu et al. 2022 (Life Science Alliance) figshare release: one zip per Vizgen
# run. Two runs carry Vizgen's cell outputs (cell_by_gene.csv with barcode-id
# columns, cell_metadata.csv); the other twelve carry only barcodes.csv, the
# decoded transcripts with no cell assignment. Those become grid bins.
# ---------------------------------------------------------------------------


def read_codebook(path: str) -> pd.DataFrame:
    """MERlin codebook: row i is barcode_id i; ``name`` is the gene or Blank-N."""
    cb = pd.read_csv(path)
    names = cb["name"].astype(str).to_numpy()
    return pd.DataFrame(
        {
            "barcode_id": np.arange(len(cb)),
            "gene_id": names,  # the release publishes symbols only
            "gene_name": names,
            "feature_type": np.where([bool(BLANK_RE.match(n)) for n in names], BLANK_FEATURE_TYPE, GENE_FEATURE_TYPE),
        }
    )


def build_liu2022_run(run: str, run_dir: str, codebook: pd.DataFrame, spec: dict, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    genome = spec["source"].get("genome", "unknown")
    var = codebook.drop(columns=["barcode_id"]).copy()
    var["genome"] = genome
    is_gene = (var.feature_type == GENE_FEATURE_TYPE).to_numpy()
    name_by_id = dict(zip(codebook.barcode_id, codebook.gene_name, strict=True))

    cbg_path = os.path.join(run_dir, "cell_by_gene.csv")
    if os.path.exists(cbg_path):
        # Segmented run: cells. Columns are barcode ids (strings of ints).
        cbg = pd.read_csv(cbg_path, index_col=0)
        meta = pd.read_csv(os.path.join(run_dir, "cell_metadata.csv"), index_col=0)
        meta.index = meta.index.astype(str)
        cbg.index = cbg.index.astype(str)
        meta = meta.loc[cbg.index]
        col_ids = [int(c) for c in cbg.columns]
        unknown = [c for c in col_ids if c not in name_by_id]
        if unknown:
            raise ValueError(f"{run}: cell_by_gene columns not in the codebook: {unknown[:5]}")
        # Fill the full codebook axis so every run in the family shares one var.
        full = np.zeros((len(cbg), len(var)), dtype=np.int64)
        full[:, col_ids] = np.round(cbg.to_numpy()).astype(np.int64)
        matrix = sp.csr_matrix(full)
        ids = np.array([f"{run}:{c}" for c in cbg.index], dtype=object)
        n_counts = np.asarray(matrix.sum(axis=1)).ravel()
        obs = pd.DataFrame(
            {
                "obs_index": np.arange(len(meta), dtype=np.int64),
                "source_obs_id": ids,
                "x_um": meta.center_x.to_numpy(),
                "y_um": meta.center_y.to_numpy(),
                "n_counts": n_counts,
                "n_genes": np.asarray((matrix[:, is_gene] > 0).sum(axis=1)).ravel(),
                "negative_control_counts": np.asarray(matrix[:, ~is_gene].sum(axis=1)).ravel(),
                "unassigned_counts": 0,
                "cell_area_um2": meta.volume.to_numpy() if "volume" in meta.columns else np.nan,
                "section_id": run,
                "donor_id": spec["source"]["runs"][run]["donor_id"],
                "panel_name": spec["panel"]["panel_name"],
                "source_extras_json": [
                    json.dumps({k: (None if pd.isna(v) else v) for k, v in r.items()})
                    for r in meta[[c for c in ("fov", "volume", "barcodeCount") if c in meta.columns]].to_dict(orient="records")
                ],
            }
        )
        unit = {"spatial_unit": "cell", "segmentation_method": spec["source"]["runs"][run].get("segmentation_method", "cell_boundary_stain"), "unit_size_um": None}
    else:
        # Unsegmented run: decoded transcripts binned on a square grid.
        edge = float(spec["source"].get("bin_um", 10.0))
        tx = pd.read_csv(os.path.join(run_dir, "barcodes.csv"), usecols=["barcode_id", "global_x", "global_y"])
        tx = tx[tx.barcode_id.isin(name_by_id)]
        bx = np.floor(tx.global_x.to_numpy() / edge).astype(np.int64)
        by = np.floor(tx.global_y.to_numpy() / edge).astype(np.int64)
        bins, inverse = np.unique(np.stack([bx, by], axis=1), axis=0, return_inverse=True)
        matrix = sp.csr_matrix(
            (np.ones(len(tx), dtype=np.int64), (inverse.ravel(), tx.barcode_id.to_numpy())),
            shape=(len(bins), len(var)),
        )
        matrix.sum_duplicates()
        ids = np.array([f"{run}:bin_{x}_{y}" for x, y in bins], dtype=object)
        n_counts = np.asarray(matrix.sum(axis=1)).ravel()
        obs = pd.DataFrame(
            {
                "obs_index": np.arange(len(bins), dtype=np.int64),
                "source_obs_id": ids,
                "x_um": (bins[:, 0] + 0.5) * edge,
                "y_um": (bins[:, 1] + 0.5) * edge,
                "unit_size_um": edge,
                "n_counts": n_counts,
                "n_genes": np.asarray((matrix[:, is_gene] > 0).sum(axis=1)).ravel(),
                "negative_control_counts": np.asarray(matrix[:, ~is_gene].sum(axis=1)).ravel(),
                "unassigned_counts": 0,
                "section_id": run,
                "donor_id": spec["source"]["runs"][run]["donor_id"],
                "panel_name": spec["panel"]["panel_name"],
                "source_extras_json": json.dumps({"bin_um": edge, "n_transcripts": int(len(tx))}),
            }
        )
        unit = {"spatial_unit": "bin", "segmentation_method": "grid", "unit_size_um": edge}

    sample = section_key(run)
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    write_10x_h5(os.path.join(out_dir, "cell_feature_matrix.h5"), matrix, ids, var, genome)
    var.to_csv(os.path.join(out_dir, "cell_feature_matrix_var.csv"), index=False)
    print(f"  {run}: {len(obs)} {unit['spatial_unit']}s, median {np.median(n_counts):.0f} counts")
    return {
        "sample": sample,
        "section_id": run,
        "donor_id": spec["source"]["runs"][run]["donor_id"],
        "donor": {"donor_id": spec["source"]["runs"][run]["donor_id"], "sex": spec["source"]["runs"][run].get("sex", "unknown"), "genotype": None},
        "image_file": None,
        "n_cells": int(len(obs)),
        "n_genes_panel": int(is_gene.sum()),
        "n_features": int(len(var)),
        "median_transcripts_per_cell": float(np.median(n_counts)),
        **unit,
    }


def build_liu2022(spec: dict, src: str, out: str) -> list[dict]:
    codebook = read_codebook(os.path.join(src, "codebook.csv"))
    print(f"{spec['dataset_key']}: codebook {len(codebook)} entries, "
          f"{int((codebook.feature_type == GENE_FEATURE_TYPE).sum())} genes")
    geometry = []
    for run in spec["source"]["runs"]:
        run_dir = os.path.join(src, run)
        os.makedirs(run_dir, exist_ok=True)
        unzip_flat(os.path.join(src, f"{run}.zip"), run_dir)
        geometry.append(build_liu2022_run(run, run_dir, codebook, spec, os.path.join(out, section_key(run))))
    return geometry


BUILDERS = {"allen_abc": build_allen, "merscope_outs": build_merscope_outs, "liu2022_figshare": build_liu2022}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--source", help="override the fetched-files directory")
    parser.add_argument("--out", help="override the staging directory")
    parser.add_argument("--list-sources", action="store_true", help="print '<url>\\t<destination>' per source file and exit")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")
    if args.list_sources:
        for url, rel in sources_for(spec):
            print(f"{url}\t{os.path.join(source, rel)}")
        return

    os.makedirs(out, exist_ok=True)
    layout = spec["source"].get("layout", "allen_abc")
    geometry = BUILDERS[layout](spec, source, out)
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(geometry, handle, indent=2)
    print(f"\n{len(geometry)} section(s), {sum(g['n_cells'] for g in geometry)} cells")


if __name__ == "__main__":
    main()
