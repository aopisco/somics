#!/usr/bin/env python3
"""Turn one HuBMAP MIBI lab-submission dataset into a data package, spec-driven.

MIBI is the fourth builder shape here and the first whose per-cell values are
*computed* rather than read. A lab-submission dataset carries a 47-channel ion
count stack, a cell segmentation mask, and the cohort's single-cell table --
495k cells over 211 fields of view with no column naming this dataset's field,
so that table cannot be joined to this image (see the spec generator). What is
knowable from the dataset alone is knowable exactly, and that is what the
package holds:

- **An obs row is one label of the mask.** Its centroid and area come from the
  label's pixels; ``x_um``/``y_um`` from the pixel size the assay metadata TSV
  states (391 nm, checked against field width / image width).
- **The protein readout is the summed ion count per channel per cell.** MIBI
  pixels are pulse counts of a metal tag (``signal_type: pulse count``,
  int16), so the sum over a cell's pixels is an integer count and goes into the
  ``counts`` layer as-is -- no rounding, unlike QuPath's fractional means in the
  Monkman package. A mean per pixel is ``counts / area_px`` and is left to the
  reader.
- **All 47 channels are features.** The 37 antibody channels carry their
  UniProt accession and RRID straight from ``extras/antibodies.tsv``; the ten
  elemental and background channels (Au, Ca, Co, Fe, Ir, Na, Sc, Si, Ta,
  background) are kept and flagged ``is_control``, as Monkman keeps its blank
  cycles -- a reader wanting the gold or the background per cell has them.
- **The image is the whole stack**, rewritten from the OME-TIFF's (C, Y, X) to
  the (Y, X, C) the atlas's slab loader boxes, int16 -> uint16 (no value is
  negative). ``channel_names`` are the OME channel names in stored order.

``segmentation_method`` stays null: the submission does not say how the mask
was made, and HuBMAP's own re-segmentation (DeepCell, in the derived
``MIBI [DeepCell + SPRM]`` datasets) is a different mask.

Sources are the staged copies under ``s3://somics-dev/hubmap/<HBM-ID>/`` and are
fetched with the AWS CLI when absent, into fixed destination names so a runner
can pre-fetch them.

Run:
    python scripts/build_mibi_package.py --spec specs/mibi/<dataset>.json [--out DIR]
    python scripts/build_mibi_package.py --spec ... --list-sources
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess

import numpy as np
import pandas as pd
import tifffile

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

# Destination names inside extracted/<sample>/, keyed as the spec's `files` are.
DEST = {
    "stack": "3D_image_stack.ome.tiff",
    "mask": "cluster_labels_image.tif",
    "antibodies": "antibodies.tsv",
    "channel_report": "channelnames_report.csv",
    "metadata_tsv": "assay_metadata.tsv",
    "metadata_json": "metadata.json",
}


def sources_for(spec: dict, sample: str) -> list[tuple[str, str]]:
    """``(s3 uri, relative destination)`` pairs a sample needs on disk."""
    files = spec["samples"][sample]["files"]
    return [(files[k], DEST[k]) for k in DEST if k in files]


def fetch(uri: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["aws", "s3", "cp", uri, dest + ".part", "--only-show-errors"], check=True)
    os.replace(dest + ".part", dest)
    return dest


def read_assay_metadata(path: str) -> dict:
    with open(path) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"{path}: expected one metadata row, found {len(rows)}")
    return rows[0]


def pixel_size_um(meta: dict, width_px: int) -> float:
    """Microns per pixel from the assay metadata, cross-checked against the field size."""
    unit = meta["pixel_size_x_unit"].strip().lower()
    scale = {"nm": 1e-3, "um": 1.0, "µm": 1.0}[unit]
    px = float(meta["pixel_size_x_value"]) * scale
    py = (
        float(meta["pixel_size_y_value"])
        * {"nm": 1e-3, "um": 1.0, "µm": 1.0}[meta["pixel_size_y_unit"].strip().lower()]
    )
    if abs(px - py) > 1e-6:
        raise ValueError(f"anisotropic pixels ({px} x {py} um) are not supported")
    if meta.get("max_x_width_value"):
        field_um = (
            float(meta["max_x_width_value"])
            * {"um": 1.0, "mm": 1e3}[meta["max_x_width_unit"].strip().lower()]
        )
        derived = field_um / width_px
        if abs(derived - px) / px > 0.02:
            raise ValueError(
                f"pixel size {px:.4f} um disagrees with field width / image width "
                f"{derived:.4f} um; the metadata does not describe this image"
            )
    return px


def read_stack(path: str) -> tuple[np.ndarray, list[str]]:
    """The (C, Y, X) stack and its OME channel names, in stored order."""
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if series.axes not in ("CYX", "IYX", "ZYX"):
            raise NotImplementedError(f"{path}: axes {series.axes!r}, expected a (C, Y, X) stack")
        names = re.findall(r'<Channel[^>]*Name="([^"]+)"', tif.ome_metadata or "")
        stack = series.asarray()
    if len(names) != stack.shape[0]:
        raise ValueError(f"{path}: {len(names)} OME channel names for {stack.shape[0]} planes")
    if stack.min() < 0:
        raise ValueError(f"{path}: negative ion counts")
    return stack, names


def per_cell(mask: np.ndarray, stack: np.ndarray) -> tuple[pd.DataFrame, np.ndarray]:
    """Centroid, area and per-channel summed counts for every mask label."""
    n = int(mask.max())
    flat = mask.ravel().astype(np.int64)
    area = np.bincount(flat, minlength=n + 1)[1:]
    labels = np.arange(1, n + 1)
    present = area > 0
    ys, xs = np.indices(mask.shape)
    cy = np.bincount(flat, weights=ys.ravel(), minlength=n + 1)[1:]
    cx = np.bincount(flat, weights=xs.ravel(), minlength=n + 1)[1:]
    counts = np.stack(
        [
            np.bincount(flat, weights=stack[c].ravel().astype(np.float64), minlength=n + 1)[1:]
            for c in range(stack.shape[0])
        ],
        axis=1,
    )
    geom = pd.DataFrame(
        {
            "label": labels[present],
            "area_px": area[present],
            "y_px": cy[present] / area[present],
            "x_px": cx[present] / area[present],
        }
    )
    return geom, counts[present].round().astype(np.uint32)


def write_image(stack: np.ndarray, names: list[str], out: str) -> None:
    """(C, Y, X) int16 -> tiled (Y, X, C) uint16, the layout the slab loader boxes."""
    if os.path.exists(out):
        return
    arr = np.ascontiguousarray(np.moveaxis(stack, 0, -1)).astype(np.uint16)
    tifffile.imwrite(
        out + ".part",
        arr,
        tile=(512, 512),
        compression="zlib",
        compressionargs={"level": 1},
        photometric="minisblack",
        planarconfig="contig",
        metadata={"axes": "YXC", "channel_names": names},
    )
    os.replace(out + ".part", out)
    print(f"  wrote {os.path.basename(out)}: {arr.shape} ({os.path.getsize(out) / 1e6:.0f} MB)")


def build_var(names: list[str], antibodies: pd.DataFrame, report: pd.DataFrame) -> pd.DataFrame:
    """One row per stack channel, in matrix column order.

    Antibody channels get the accession and RRID the submission publishes;
    everything else is an elemental or background channel and is a control.
    """
    ab = antibodies.set_index(antibodies["channel_id"].str.strip())
    mass = dict(zip(report["Target"].str.strip(), report["Mass"], strict=True))
    rows = []
    for name in names:
        hit = ab.loc[name] if name in ab.index else None
        rows.append(
            {
                "var_index": name,
                "target_name": name,
                "is_control": hit is None,
                "antibody_name": None if hit is None else hit["antibody_name"],
                "uniprot_accession": None
                if hit is None or not str(hit.get("uniprot_accession_number", "")).strip()
                else str(hit["uniprot_accession_number"]).strip(),
                "antibody_rrid": None if hit is None else hit.get("rr_id"),
                "channel_mass": mass.get(name),
            }
        )
    var = pd.DataFrame(rows)
    missing = set(ab.index) - set(names)
    if missing:
        raise ValueError(f"antibodies with no stack channel: {sorted(missing)}")
    return var


def build_sample(sample: str, spec: dict, source: str, out_dir: str) -> dict:
    print(f"{sample}:")
    os.makedirs(out_dir, exist_ok=True)
    entry = spec["samples"][sample]
    sample_dir = os.path.join(source, sample)
    for uri, rel in sources_for(spec, sample):
        fetch(uri, os.path.join(sample_dir, rel))

    meta = read_assay_metadata(os.path.join(sample_dir, DEST["metadata_tsv"]))
    stack, names = read_stack(os.path.join(sample_dir, DEST["stack"]))
    mask = tifffile.imread(os.path.join(sample_dir, DEST["mask"]))
    if mask.shape != stack.shape[1:]:
        raise ValueError(f"{sample}: mask {mask.shape} does not match the stack {stack.shape[1:]}")
    height, width = mask.shape
    px_um = pixel_size_um(meta, width)

    geom, counts = per_cell(mask, stack)
    antibodies = pd.read_csv(os.path.join(sample_dir, DEST["antibodies"]), sep="\t")
    report = pd.read_csv(os.path.join(sample_dir, DEST["channel_report"]))
    var = build_var(names, antibodies, report)

    obs = pd.DataFrame(
        {
            "obs_index": np.arange(len(geom), dtype=np.int64),
            # Mask labels are unique within the field, and the field is the dataset.
            "source_obs_id": [f"{sample}:{int(label)}" for label in geom["label"]],
            "x_um": geom["x_px"].to_numpy() * px_um,
            "y_um": geom["y_px"].to_numpy() * px_um,
            "x_px": geom["x_px"].to_numpy(),
            "y_px": geom["y_px"].to_numpy(),
            "pixel_size_um": px_um,
            "cell_area_um2": geom["area_px"].to_numpy() * px_um * px_um,
            "section_id": entry["section_id"],
            "donor_id": entry["donor_id"],
            "panel_name": spec["panel"]["panel_name"],
        }
    )
    obs["source_extras_json"] = [
        json.dumps(
            {
                "mask_label": int(label),
                "area_px": int(area),
                "roi_description": meta.get("roi_description"),
                "roi_id": meta.get("roi_id"),
            }
        )
        for label, area in zip(geom["label"], geom["area_px"], strict=True)
    ]
    obs.to_csv(os.path.join(out_dir, f"{sample}_obs.csv"), index=False)
    print(f"  wrote {sample}_obs.csv: {len(obs)} cells")
    var.to_csv(os.path.join(out_dir, f"{sample}_var.csv"), index=False)
    print(
        f"  wrote {sample}_var.csv: {len(var)} channels, {int((~var.is_control).sum())} antibodies"
    )
    pd.DataFrame(counts, columns=names).to_csv(
        os.path.join(out_dir, f"{sample}_protein_counts.csv"), index=False
    )

    image_file = f"{sample}_{spec['image_modality']}_image.tif"
    write_image(stack, names, os.path.join(out_dir, image_file))
    for rel in (DEST["metadata_tsv"], DEST["antibodies"], DEST["channel_report"]):
        dest = os.path.join(out_dir, rel)
        if not os.path.exists(dest):
            try:
                os.link(os.path.join(sample_dir, rel), dest)
            except OSError:
                import shutil

                shutil.copy2(os.path.join(sample_dir, rel), dest)

    return {
        "sample": sample,
        "image_file": image_file,
        "image_source": spec["download_url"],
        "n_channels": len(names),
        "channel_names": names,
        "n_cells": int(len(obs)),
        "n_features": int(len(var)),
        "n_antibodies": int((~var.is_control).sum()),
        "pixel_size_um": px_um,
        "height_px": int(height),
        "width_px": int(width),
        "section_id": entry["section_id"],
        "donor_id": entry["donor_id"],
        "acquisition_instrument": meta.get("acquisition_instrument_model"),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--samples", nargs="*")
    parser.add_argument("--source")
    parser.add_argument("--out")
    parser.add_argument("--list-sources", action="store_true")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    source = args.source or os.path.join(DATA_HOME, "datasets", key, "extracted")
    out = args.out or os.path.join(DATA_HOME, "datasets", key, "staging")
    samples = args.samples or list(spec["samples"])
    if args.list_sources:
        for sample in samples:
            for uri, rel in sources_for(spec, sample):
                print(f"{uri}\t{os.path.join(source, sample, rel)}")
        return

    os.makedirs(out, exist_ok=True)
    summary = [build_sample(s, spec, source, os.path.join(out, s)) for s in samples]
    with open(os.path.join(out, "sample_geometry.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    print(f"\n{len(summary)} sample(s), {sum(e['n_cells'] for e in summary)} cells")


if __name__ == "__main__":
    main()
