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

Sources are fetched rather than assumed present. A spec names them in one of
two ways:

- **Study templates** (LIBD): ``counts_url`` / ``image_url`` / ``spatial_url``
  with ``{sample}``, plus ``spatial_files`` to fetch one by one -- a study
  split across hosts.
- **Per-sample files** (the 10x catalogue): ``samples[s]["files"]`` with
  ``counts`` (h5), ``spatial`` (the ``spatial.tar.gz`` Space Ranger writes),
  ``image``, or for Visium HD ``binned_outputs`` -- the tarball that carries
  the 2, 8 and 16 um bins. Only the bin the spec asks for (``hd_bin_um``) is
  extracted: counts h5 plus its ``spatial/`` directory.

Three layout differences between Space Ranger releases are absorbed here so
the spec never has to know which pipeline wrote a dataset: the positions file
is ``tissue_positions_list.csv`` (headerless, 1.x), ``tissue_positions.csv``
(with header, 2.x) or ``tissue_positions.parquet`` (HD); the image is a TIFF,
a BigTIFF ``.btf`` or, on three 1.3.0 FFPE releases, a JPEG that is converted
to a tiled TIFF because the atlas's image loader streams TIFF slabs; and the
pixel size comes from ``microns_per_pixel`` where the scale factors carry it
(HD, 3.x) and is derived from the spot diameter where they do not.

For Visium the micron frame is derived, not published. `scalefactors_json.json`
gives `spot_diameter_fullres` — the spot's diameter in full-resolution pixels —
and a Visium spot is 55 um across by construction, so

    pixel_size_um = 55.0 / spot_diameter_fullres

and micron coordinates follow from the full-resolution pixel columns. That is
the same relation the atlas records for these sections. For Visium HD the
scale factors publish `microns_per_pixel` directly and it is used as-is; where
both are available they are checked against each other.

The image must be the frame the pixel coordinates live in. On CytAssist runs
that is the microscope image (`tissue_image`), not the instrument's own
low-resolution capture (`image.tif`); the resolver picks accordingly, and this
script refuses a sample whose coordinates fall outside the image it was given.

Run:
    python scripts/build_visium_package.py --spec specs/<dataset>.json \\
        [--samples ...] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import urllib.request

import h5py
import numpy as np
import pandas as pd
import tifffile

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
UA = {"User-Agent": "somics/0.1 (mailto:aoliveirapisco@chanzuckerberg.com)"}
# 10x's Cloudflare front rejects bare agents (see CLAUDE.md, "Hosts disagree
# about user agents"); everything else here is happy with the honest one.
BROWSER_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
POSITION_FILES = (
    "tissue_positions.parquet",
    "tissue_positions.csv",
    "tissue_positions_list.csv",
    "tissue_positions_list.txt",
)

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
    headers = BROWSER_UA if "10xgenomics.com" in url else UA
    req = urllib.request.Request(url, headers=headers)
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


def sources_for(spec: dict, sample: str) -> list[tuple[str, str]]:
    """``(url, relative destination)`` pairs a sample needs on disk.

    The destination names are fixed so a caller (the EC2 runner) can pre-fetch
    with its own downloader and this script finds the files already present.
    """
    entry = spec["samples"][sample]
    files = entry.get("files")
    if files:
        out = []
        if "counts" in files:
            out.append((files["counts"], "filtered_feature_bc_matrix.h5"))
        if "spatial" in files:
            out.append((files["spatial"], "spatial.tar.gz"))
        if "binned_outputs" in files:
            out.append((files["binned_outputs"], "binned_outputs.tar.gz"))
        ext = os.path.splitext(files["image"].split("?")[0])[1].lower() or ".tif"
        out.append((files["image"], f"full_image{ext}"))
        return out
    out = [
        (spec["counts_url"].format(sample=sample), "filtered_feature_bc_matrix.h5"),
        (spec["image_url"].format(sample=sample), "full_image.tif"),
    ]
    out += [(spec["spatial_url"].format(sample=sample, file=f), f) for f in spec["spatial_files"]]
    return out


def image_source_url(spec: dict, sample: str) -> str:
    files = spec["samples"][sample].get("files")
    return files["image"] if files else spec["image_url"].format(sample=sample)


def extract_spatial(sample_dir: str) -> str:
    """Unpack ``spatial.tar.gz`` if present; return the directory holding the
    positions and scale factors (``spatial/`` or the sample dir itself)."""
    tar_path = os.path.join(sample_dir, "spatial.tar.gz")
    spatial = os.path.join(sample_dir, "spatial")
    if os.path.exists(tar_path) and not os.path.isdir(spatial):
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(sample_dir, filter="data")
    if os.path.isdir(spatial):
        return spatial
    return sample_dir


def extract_hd_bin(sample_dir: str, bin_um: int) -> str:
    """Pull one bin size out of ``binned_outputs.tar.gz`` and return its directory.

    The tarball is ~14 GB of gzip and carries every bin size; it is streamed
    once and only ``square_{bin}um/filtered_feature_bc_matrix.h5`` and that
    bin's ``spatial/`` directory are written. Nothing else in it is used.
    """
    tag = f"square_{bin_um:03d}um"
    dest = os.path.join(sample_dir, tag)
    wanted = ("filtered_feature_bc_matrix.h5", "spatial/scalefactors_json.json") + tuple(
        f"spatial/{n}" for n in POSITION_FILES
    )
    if os.path.exists(os.path.join(dest, "filtered_feature_bc_matrix.h5")) and any(
        os.path.exists(os.path.join(dest, "spatial", n)) for n in POSITION_FILES
    ):
        return dest
    tar_path = os.path.join(sample_dir, "binned_outputs.tar.gz")
    print(f"  extracting {tag} from binned_outputs.tar.gz")
    with tarfile.open(tar_path, "r|gz") as tar:
        for member in tar:
            if f"/{tag}/" not in member.name and not member.name.startswith(f"{tag}/"):
                continue
            rel = member.name.split(f"{tag}/", 1)[1]
            if rel not in wanted or not member.isfile():
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 22)
    if not os.path.exists(os.path.join(dest, "filtered_feature_bc_matrix.h5")):
        raise FileNotFoundError(f"{tar_path}: no {tag}/filtered_feature_bc_matrix.h5 inside")
    return dest


def read_positions(spatial_dir: str) -> pd.DataFrame:
    """Tissue positions in whichever layout Space Ranger wrote them.

    1.x: ``tissue_positions_list.csv`` / ``.txt``, no header. 2.x: ``tissue_positions.csv``
    with a header. HD: ``tissue_positions.parquet``. Same six columns throughout.
    """
    for name in POSITION_FILES:
        path = os.path.join(spatial_dir, name)
        if os.path.exists(path):
            break
    else:
        raise FileNotFoundError(f"{spatial_dir}: no tissue positions file ({POSITION_FILES})")
    if path.endswith(".parquet"):
        positions = pd.read_parquet(path)
    else:
        with open(path) as handle:
            has_header = handle.readline().split(",")[0].strip().lower() == "barcode"
        positions = pd.read_csv(path, header=0 if has_header else None)
    positions.columns = POSITION_COLUMNS
    positions["barcode"] = positions["barcode"].astype(str)
    return positions


def pixel_size_of(scale: dict, unit_um: float) -> float:
    """Full-resolution pixel size in um, from the scale factors.

    HD and Space Ranger >= 3 publish ``microns_per_pixel``; older releases only
    give the spot diameter in pixels, and a Visium spot is 55 um across. Where
    both are present they must agree, or the image is not the frame the
    positions were written in.
    """
    derived = (
        unit_um / float(scale["spot_diameter_fullres"])
        if "spot_diameter_fullres" in scale
        else None
    )
    published = float(scale["microns_per_pixel"]) if "microns_per_pixel" in scale else None
    if published is None:
        if derived is None:
            raise KeyError(
                "scalefactors_json.json has neither microns_per_pixel nor spot_diameter_fullres"
            )
        return derived
    if (
        derived is not None
        and abs(derived - published) / published > 0.02
        and "bin_size_um" not in scale
    ):
        raise ValueError(
            f"microns_per_pixel {published:.4f} disagrees with 55 um / spot_diameter_fullres "
            f"{derived:.4f}; the image and positions are not in one frame"
        )
    return published


def ensure_tiff(path: str) -> str:
    """The atlas's image loader streams TIFF slabs of a (Y, X[, C]) image.

    Two source shapes need a one-time rewrite: a JPEG (three 1.3.0 FFPE
    releases), and a channels-first fluorescence stack -- 10x's 1.2.0
    immunofluorescence releases are one TIFF page per stain, which tifffile
    reads as (I, Y, X). Both are written once as a tiled (Y, X, C) TIFF beside
    the original; the original is what gets staged to raw/.
    """
    if not path.lower().endswith((".jpg", ".jpeg")):
        return ensure_channels_last(path)
    tif_path = os.path.splitext(path)[0] + ".tif"
    if os.path.exists(tif_path):
        return tif_path
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    print(f"  converting {os.path.basename(path)} to tiled TIFF")
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"))
    tifffile.imwrite(
        tif_path + ".part", arr, tile=(1024, 1024), compression="zlib", photometric="rgb"
    )
    os.replace(tif_path + ".part", tif_path)
    return tif_path


def ensure_channels_last(path: str) -> str:
    """Rewrite a (C, Y, X) stack as (Y, X, C); pass anything already (Y, X[, C]) through."""
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        axes, shape = series.axes, series.shape
    if axes in ("YX", "YXS", "YXC"):
        return path
    if len(shape) == 3 and axes[1:] == "YX" and shape[0] <= 16:
        out = os.path.splitext(path)[0] + "_yxc.tif"
        if os.path.exists(out):
            return out
        print(f"  rewriting {os.path.basename(path)} {axes}{shape} as (Y, X, C)")
        stack = tifffile.imread(path)
        arr = np.ascontiguousarray(np.moveaxis(stack, 0, -1))
        tifffile.imwrite(
            out + ".part",
            arr,
            tile=(1024, 1024),
            compression="zlib",
            photometric="minisblack",
            planarconfig="contig",
            metadata={"axes": "YXC"},
        )
        os.replace(out + ".part", out)
        return out
    raise NotImplementedError(f"{path}: image axes {axes!r} with shape {shape} are not (Y, X[, C])")


def check_frame(sample, spatial_dir, scale, height, width, x_px, y_px) -> None:
    """Refuse an image that is not the frame the positions were written in.

    Space Ranger writes ``tissue_hires_image.png`` by scaling the full-resolution
    image it was given by ``tissue_hires_scalef``, so the hires size divided by
    that factor is the size of the frame the positions live in. An image of a
    different size is a different image -- on CytAssist runs, typically the
    instrument's own capture instead of the microscope scan. Where no hires
    image was staged (LIBD), fall back to requiring the spots to fit.
    """
    hires = os.path.join(spatial_dir, "tissue_hires_image.png")
    if os.path.exists(hires) and "tissue_hires_scalef" in scale:
        from PIL import Image

        with Image.open(hires) as im:
            hw, hh = im.size
        implied_w, implied_h = hw / scale["tissue_hires_scalef"], hh / scale["tissue_hires_scalef"]
        tol_w, tol_h = max(4, 0.01 * implied_w), max(4, 0.01 * implied_h)
        if abs(implied_w - width) > tol_w or abs(implied_h - height) > tol_h:
            raise ValueError(
                f"{sample}: the positions' frame is {implied_w:.0f}x{implied_h:.0f} px (from "
                f"tissue_hires_image.png / tissue_hires_scalef) but the image is {width}x{height}; "
                f"it is not the image Space Ranger was given"
            )
        return
    if x_px.max() > width or y_px.max() > height:
        raise ValueError(
            f"{sample}: positions extend to ({x_px.max():.0f}, {y_px.max():.0f}) px but the image "
            f"is {width}x{height} and no hires image is available to check the frame"
        )


def pad_image(path: str, height: int, width: int, modality: str) -> str:
    """Write a copy of the image padded (bottom/right) with background to ``height x width``.

    White for brightfield, black for fluorescence -- the value a crop with no
    tissue under it would carry anyway. Written once beside the original.
    """
    out = os.path.splitext(path)[0] + "_padded.tif"
    if os.path.exists(out):
        return out
    arr = tifffile.imread(path)
    if arr.ndim == 3 and arr.shape[0] <= 16 and arr.shape[1] > 16:
        arr = np.moveaxis(arr, 0, -1)
    fill = np.iinfo(arr.dtype).max if (modality == "he" and arr.dtype.kind == "u") else 0
    shape = (height, width) + arr.shape[2:]
    canvas = np.full(shape, fill, dtype=arr.dtype)
    canvas[: arr.shape[0], : arr.shape[1]] = arr
    del arr
    tifffile.imwrite(
        out + ".part",
        canvas,
        tile=(1024, 1024),
        compression="zlib",
        photometric="rgb" if (canvas.ndim == 3 and canvas.shape[2] == 3) else "minisblack",
        planarconfig="contig" if canvas.ndim == 3 else None,
    )
    os.replace(out + ".part", out)
    return out


def build_sample(sample: str, spec: dict, source: str, out_dir: str) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    entry = spec["samples"][sample]

    sample_dir = os.path.join(source, sample)
    for url, rel in sources_for(spec, sample):
        fetch(url, os.path.join(sample_dir, rel))

    if "binned_outputs" in entry.get("files", {}):
        bin_um = int(spec["hd_bin_um"])
        data_dir = extract_hd_bin(sample_dir, bin_um)
        counts = os.path.join(data_dir, "filtered_feature_bc_matrix.h5")
        spatial_dir = os.path.join(data_dir, "spatial")
    else:
        counts = os.path.join(sample_dir, "filtered_feature_bc_matrix.h5")
        spatial_dir = extract_spatial(sample_dir)
    image = next(
        os.path.join(sample_dir, f)
        for f in sorted(os.listdir(sample_dir))
        if f.startswith("full_image.") and not f.endswith(".part")
    )
    image = ensure_tiff(image)

    scale = json.load(open(os.path.join(spatial_dir, "scalefactors_json.json")))
    unit = float(spec["unit_size_um"])
    if "bin_size_um" in scale and float(scale["bin_size_um"]) != unit:
        raise ValueError(f"{sample}: extracted bin is {scale['bin_size_um']} um, spec says {unit}")
    pixel_size = pixel_size_of(scale, unit)
    diameter = float(scale.get("spot_diameter_fullres", unit / pixel_size))

    positions = read_positions(spatial_dir)

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

    with tifffile.TiffFile(image) as tif:
        height, width = (int(d) for d in tif.series[0].levels[0].shape[:2])
    check_frame(sample, spatial_dir, scale, height, width, x_px, y_px)
    # CytAssist detects tissue on its own instrument image, which covers the
    # whole capture area; the microscope scan Space Ranger was given may not.
    # Spots past the scan's edge are real measurements with no pixels under
    # them. Every obs row must be placeable in the image (the loader refuses
    # otherwise), so the image is padded with background to the spot extent
    # and the crops there are honestly blank. Recorded in the geometry.
    padded_from = None
    need_h = int(np.ceil(y_px.max())) + 64 + 1
    need_w = int(np.ceil(x_px.max())) + 64 + 1
    if need_h > height or need_w > width:
        image = pad_image(
            image, max(need_h, height), max(need_w, width), spec.get("image_modality", "he")
        )
        padded_from = (height, width)
        with tifffile.TiffFile(image) as tif:
            height, width = (int(d) for d in tif.series[0].levels[0].shape[:2])
        print(
            f"  padded image {padded_from} -> ({height}, {width}) to cover spots past the scan edge"
        )

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
    extras = {k: entry[k] for k in ("position_um", "replicate") if k in entry}
    if "bin_size_um" in scale:
        extras["bin_size_um"] = float(scale["bin_size_um"])
    obs["source_extras_json"] = [
        json.dumps({"array_row": int(r), "array_col": int(c), **extras})
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
    image_file = f"{sample}_{spec.get('image_modality', 'he')}_image.tif"
    for src, name in ((counts, "filtered_feature_bc_matrix.h5"), (image, image_file)):
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest):
            continue
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)

    with tifffile.TiffFile(image) as tif:
        shape = tif.series[0].shape
    return {
        "sample": sample,
        "image_file": image_file,
        "image_source": image_source_url(spec, sample),
        "n_channels": int(shape[2]) if len(shape) == 3 else 1,
        "padded_from_hw": list(padded_from) if padded_from else None,
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
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="print '<url>\\t<destination path>' per source file and exit (for a pre-fetcher)",
    )
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")

    samples = args.samples or list(spec["samples"])
    unknown = [s for s in samples if s not in spec["samples"]]
    if unknown:
        raise SystemExit(f"not in {args.spec}: {unknown}")
    if args.list_sources:
        for sample in samples:
            for url, rel in sources_for(spec, sample):
                print(f"{url}\t{os.path.join(source, sample, rel)}")
        return

    os.makedirs(out, exist_ok=True)
    summary = [build_sample(s, spec, source, os.path.join(out, s)) for s in samples]
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    print(f"\n{len(summary)} sample(s), {sum(e['n_spots'] for e in summary)} spots")


if __name__ == "__main__":
    main()
