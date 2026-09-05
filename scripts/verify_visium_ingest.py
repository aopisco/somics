#!/usr/bin/env python3
"""Check ingested Visium / Visium HD sections against their specs and sources.

An ingest can succeed and still be wrong in ways nothing downstream notices: a
section attached to the wrong image frame, an obs row whose expression vector
belongs to another spot, a disease string the resolver silently dropped. This
reads the sections a set of specs describes back out of the atlas and checks:

- **presence and grain** -- the section exists, its obs count, `technology`,
  `spatial_unit`, `unit_size_um`, organism and tissue match the spec;
- **expression alignment** -- for a sample of obs rows, the sum of the stored
  expression vector equals the row's `n_counts`, which the builder computed
  from the source h5 (a misaligned matrix fails this immediately);
- **image and frame** -- one SectionImageSchema row of the spec's modality with
  the spec's channel names, every obs `(x_px, y_px)` inside the image, and the
  crops at the highest-count spots darker (H&E) or brighter (fluorescence) than
  crops at random positions on the same image -- tissue where the coordinates
  say tissue is, which is the check the CytAssist frame trap needs;
- **disease resolution** -- on a diseased section, how many obs rows carry a
  non-null `disease` after the MONDO pass;
- **source** (`--source-check`, 10x specs only) -- barcode and feature counts
  in the staged `filtered_feature_bc_matrix.h5` under `s3://somics-dev/raw/`
  equal the atlas's, and `pixel_size_um` reproduces from the staged
  `scalefactors_json.json`. Visium HD counts live inside the 14 GB tarball and
  are not re-read here.

A crop grid per checked section is written as PNG for the eyeball pass.

Run:
    eval "$(aws configure export-credentials --profile sci-data-dev-poweruser --format env)"
    AWS_REGION=us-east-1 uv run --with s3fs python scripts/verify_visium_ingest.py \\
        --atlas s3://somics-dev/ingest/tenx_visium/atlas/<stamp> \\
        [--specs 'specs/tenx_visium/*.json'] [--source-check] [--report reports/visium_verify.md]

The S3 store reads static credentials from the environment, not an SSO
profile, hence the `export-credentials` line.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tarfile
import tempfile

import numpy as np
import polars as pl

RAW = "s3://somics-dev/raw"
SAMPLE_ROWS = 64
CROP_ROWS = 16


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []

    def add(self, section: str, check: str, ok: bool, detail: str = "") -> None:
        self.rows.append((section, check, bool(ok), detail))
        print(f"  {'ok  ' if ok else 'FAIL'} {check}: {detail}")

    def failures(self) -> list[tuple[str, str, bool, str]]:
        return [r for r in self.rows if not r[2]]

    def markdown(self, atlas: str) -> str:
        out = [f"# Visium ingest verification\n\natlas: `{atlas}`\n"]
        out.append(f"**{len(self.rows) - len(self.failures())}/{len(self.rows)} checks passed**\n")
        out.append("| section | check | result | detail |\n|---|---|---|---|")
        for s, c, ok, d in self.rows:
            out.append(f"| {s} | {c} | {'ok' if ok else '**FAIL**'} | {d} |")
        return "\n".join(out) + "\n"


def load_specs(pattern: str) -> list[dict]:
    return [json.load(open(p)) for p in sorted(glob.glob(pattern))]


def table(atlas, name: str) -> pl.DataFrame:
    return pl.from_arrow(atlas.db.open_table(name).to_arrow())


def fetch_s3(uri: str, dest: str) -> str:
    subprocess.run(["aws", "s3", "cp", uri, dest, "--only-show-errors"], check=True)
    return dest


def source_counts(key: str, sample: str, tmp: str) -> tuple[int, int, dict]:
    """(n barcodes, n features, scalefactors) from the staged 10x files."""
    import h5py

    h5 = fetch_s3(f"{RAW}/{key}/{sample}/filtered_feature_bc_matrix.h5", os.path.join(tmp, "m.h5"))
    with h5py.File(h5) as f:
        n_bc = int(f["matrix/barcodes"].shape[0])
        n_ft = int(f["matrix/features/id"].shape[0])
    tar = fetch_s3(f"{RAW}/{key}/{sample}/spatial.tar.gz", os.path.join(tmp, "s.tgz"))
    with tarfile.open(tar) as t:
        member = next(m for m in t.getmembers() if m.name.endswith("scalefactors_json.json"))
        scale = json.load(t.extractfile(member))
    return n_bc, n_ft, scale


def crop_stats(crops: np.ndarray, modality: str) -> float:
    """One number per crop set: mean intensity (H&E darker = tissue; IF brighter = tissue)."""
    return float(np.asarray(crops, dtype="float64").mean())


def image_group(atlas, section_uid: str, pointer: str) -> str | None:
    """The zarr group the section's crop pointers address.

    ``SectionImageSchema.dataset_uid`` is the package dataset, not the zarr
    group; the group name lives only on the obs pointer struct, which the
    polars view hides. Read it from the raw Lance table.
    """
    rows = (
        atlas.db.open_table("SpatialObs")
        .search()
        .where(f"section_uid = '{section_uid}' AND has_{pointer} = true")
        .select([pointer])
        .limit(1)
        .to_list()
    )
    return rows[0][pointer]["zarr_group"] if rows else None


def random_windows(
    atlas_root: str, group: str, n: int, size: int, shape: tuple[int, int], rng
) -> np.ndarray:
    """Crops at random positions, read straight from the image zarr array.

    A remote root needs an fsspec filesystem: run under ``uv run --with s3fs``.
    """
    import zarr

    arr = zarr.open_array(f"{atlas_root}/zarr_store/{group}/layers/raw", mode="r")
    out = []
    for _ in range(n):
        y = int(rng.integers(0, shape[0] - size))
        x = int(rng.integers(0, shape[1] - size))
        out.append(np.asarray(arr[y : y + size, x : x + size]))
    return np.stack(out)


def save_grid(crops: np.ndarray, path: str, title: str) -> None:
    from PIL import Image

    n = len(crops)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    c = crops
    if c.dtype != np.uint8:
        lo, hi = np.percentile(c, 1), np.percentile(c, 99.5)
        c = np.clip((c - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
    if c.ndim == 4 and c.shape[-1] not in (1, 3):
        c = c[..., :3] if c.shape[-1] > 3 else np.repeat(c[..., :1], 3, axis=-1)
    if c.ndim == 3:
        c = np.repeat(c[..., None], 3, axis=-1)
    h, w = c.shape[1:3]
    canvas = np.full((rows * h, cols * w, 3), 255, dtype=np.uint8)
    for i, crop in enumerate(c):
        r, k = divmod(i, cols)
        canvas[r * h : (r + 1) * h, k * w : (k + 1) * w] = crop[..., :3]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(canvas).save(path)


def check_section(atlas, atlas_root, spec, sample, entry, tables, report, args, tmp, rng) -> None:
    sid = entry["section_id"]
    sections, images = tables
    row = sections.filter(pl.col("section_id") == sid)
    report.add(sid, "section present", row.height == 1, f"{row.height} row(s)")
    if row.height != 1:
        return
    uid = row["uid"][0]
    hd = spec["technology"] == "visium_hd"

    report.add(
        sid,
        "tissue",
        row["tissue"][0] == entry.get("tissue", spec.get("tissue")),
        f"{row['tissue'][0]!r}",
    )
    exp_state = entry.get("disease_state", spec.get("disease_state"))
    report.add(
        sid, "disease_state", row["disease_state"][0] == exp_state, f"{row['disease_state'][0]!r}"
    )

    q = atlas.query().where(f"section_uid == '{uid}'")
    obs = q.to_polars()
    n = obs.height
    report.add(sid, "obs rows", n > 0, f"{n}")
    for col, expected in (
        ("technology", spec["technology"]),
        ("spatial_unit", spec["spatial_unit"]),
        ("organism", spec["organism"]),
    ):
        vals = obs[col].unique().to_list()
        report.add(sid, f"obs.{col}", vals == [expected], f"{vals}")
    unit = obs["unit_size_um"].unique().to_list()
    report.add(sid, "obs.unit_size_um", unit == [float(spec["unit_size_um"])], f"{unit}")
    report.add(
        sid, "n_counts > 0", bool((obs["n_counts"] > 0).all()), f"min {obs['n_counts'].min()}"
    )
    if exp_state == "diseased":
        resolved = int(obs["disease"].is_not_null().sum())
        report.add(
            sid,
            "disease resolved",
            resolved == n,
            f"{resolved}/{n} rows carry a MONDO label ({entry.get('disease')!r})",
        )

    # expression alignment: stored row sums == builder's n_counts, on a sample
    ids = obs.sort("n_counts", descending=True)["source_obs_id"].to_list()
    pick = ids[:: max(1, len(ids) // SAMPLE_ROWS)][:SAMPLE_ROWS]
    quoted = ", ".join("'" + i + "'" for i in pick)
    qs = atlas.query().where(f"section_uid == '{uid}' AND source_obs_id IN ({quoted})")
    sub = qs.to_polars()
    try:
        adata = qs.select_fields("gene_expression").to_anndata()
        x = adata.X
        sums = np.asarray(x.sum(axis=1)).ravel()
        order_ok = len(sums) == sub.height
        match = order_ok and np.allclose(sums, sub["n_counts"].to_numpy())
        report.add(
            sid,
            "expression row sums == n_counts",
            match,
            f"{len(sums)} rows sampled, {adata.n_vars} features",
        )
        n_features = int(adata.n_vars)
    except Exception as e:  # noqa: BLE001
        report.add(sid, "expression readable", False, str(e)[:120])
        n_features = -1

    # image
    im = images.filter(pl.col("section_uid") == uid)
    report.add(sid, "image row", im.height == 1, f"{im.height}")
    if im.height == 1:
        modality = im["image_modality"][0]
        report.add(sid, "image_modality", modality == spec["image_modality"], f"{modality!r}")
        if spec.get("channel_names"):
            got = im["channel_names"][0]
            got = list(got) if got is not None else None
            report.add(sid, "channel_names", got == spec["channel_names"], f"{got}")
        h, w = int(im["height_px"][0]), int(im["width_px"][0])
        inside = bool(
            (obs["x_px"] < w).all() and (obs["y_px"] < h).all() and (obs["x_px"] >= 0).all()
        )
        report.add(
            sid,
            "obs inside image",
            inside,
            f"max x {obs['x_px'].max():.0f}/{w}, max y {obs['y_px'].max():.0f}/{h}",
        )
        ps = float(im["pixel_size_um"][0])
        report.add(
            sid, "pixel_size image == obs", np.isclose(ps, obs["pixel_size_um"][0]), f"{ps:.5f}"
        )
        pointer = "he_crop" if modality == "he" else "morphology_crop"
        try:
            top = ids[:CROP_ROWS]
            qt = atlas.query().where(
                f"section_uid == '{uid}' AND source_obs_id IN "
                f"({', '.join(chr(39) + i + chr(39) for i in top)})"
            )
            crops = np.stack(qt.to_spatial_batch(pointer).layers["raw"])
            report.add(sid, f"{pointer} shape", crops.shape[1:3] == (128, 128), f"{crops.shape}")
            group = image_group(atlas, uid, pointer)
            rand = random_windows(atlas_root, group, len(crops), 128, (h, w), rng)
            at, away = crop_stats(crops, modality), crop_stats(rand, modality)
            tissue_where_expected = at < away if modality == "he" else at > away
            report.add(
                sid,
                "crops land on tissue",
                tissue_where_expected,
                f"mean intensity at top spots {at:.1f} vs random {away:.1f}",
            )
            save_grid(crops, os.path.join(args.crops_dir, f"{sid}_{pointer}.png"), sid)
        except Exception as e:  # noqa: BLE001
            report.add(sid, f"{pointer} readable", False, str(e)[:160])

    if args.source_check and "files" in entry and not hd:
        try:
            n_bc, n_ft, scale = source_counts(spec["dataset_key"], sample, tmp)
            report.add(sid, "source barcodes == obs rows", n_bc == n, f"{n_bc} vs {n}")
            report.add(sid, "source features == var", n_ft == n_features, f"{n_ft} vs {n_features}")
            exp_ps = float(
                scale.get("microns_per_pixel")
                or spec["unit_size_um"] / scale["spot_diameter_fullres"]
            )
            report.add(
                sid,
                "pixel_size from scalefactors",
                np.isclose(exp_ps, obs["pixel_size_um"][0], rtol=1e-6),
                f"{exp_ps:.5f}",
            )
        except Exception as e:  # noqa: BLE001
            report.add(sid, "source check", False, str(e)[:160])


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--atlas", required=True, help="atlas root, local path or s3://")
    ap.add_argument("--specs", default="specs/tenx_visium/*.json")
    ap.add_argument("--only", nargs="*", help="dataset_key values to check")
    ap.add_argument("--source-check", action="store_true")
    ap.add_argument("--report", default="reports/visium_verify.md")
    ap.add_argument("--crops-dir", default="reports/visium_crops")
    args = ap.parse_args()

    from homeobox import RaggedAtlas

    atlas = RaggedAtlas.checkout_latest(args.atlas)
    sections = table(atlas, "TissueSectionSchema")
    images = table(atlas, "SectionImageSchema")
    print(f"atlas: {sections.height} sections, {images.height} images")

    specs = load_specs(args.specs)
    if args.only:
        specs = [s for s in specs if s["dataset_key"] in set(args.only)]
    report = Report()
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as tmp:
        for spec in specs:
            for sample, entry in spec["samples"].items():
                print(f"\n{spec['dataset_key']} / {entry['section_id']}")
                check_section(
                    atlas,
                    args.atlas.rstrip("/"),
                    spec,
                    sample,
                    entry,
                    (sections, images),
                    report,
                    args,
                    tmp,
                    rng,
                )

    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w") as f:
        f.write(report.markdown(args.atlas))
    fails = report.failures()
    print(
        f"\n{len(report.rows) - len(fails)}/{len(report.rows)} checks passed; "
        f"report at {args.report}"
    )
    for s, c, _, d in fails:
        print(f"  FAIL {s}: {c} -- {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
