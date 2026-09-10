#!/usr/bin/env python3
"""Write one builder spec per staged HuBMAP seqFISH dataset (Cai lab), one section per field of view.

Six of HuBMAP's 18 seqFISH datasets are buildable (docs/2026-09-09_merfish_adapter.md,
"seqFISH"): three small-intestine and three spleen datasets from donor W105 with
per-FOV count matrices, one centroid table, a DAPI stack and a segmentation mask
per FOV. The other twelve are indexed-but-404 or ship no count matrices.

**One section per field of view.** Cell centroids are in FOV-local pixels
(0-2048 at 0.112 um/px) and the stage positions that would place the FOVs on the
slide are not recoverable: the ``.pos`` files list 15-16 Micro-Manager positions
across several tissues with labels that do not map onto the dataset's ``posN``
numbering, and the MMStack OME headers carry no per-plane position. So each FOV
is its own section (``<HBM-ID>_fov<N>``) with its own DAPI image, which is the
same tile-grid shape planned for HuBMAP imagery.

Run:
    python scripts/make_seqfish_specs.py            # writes specs/seqfish/*.json
"""

from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd
import s3fs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_hubmap_xenium_specs import donor_from  # noqa: E402

REGISTRY = "data/datasets.csv"
BUCKET = "somics-dev"
PIXEL_SIZE_UM = 0.112  # PhysicalSizeX of the MMStack OME headers (2048 px = 229 um)
REGION_RE = re.compile(r"W105 (small bowel|spleen), (\w+) seqFISH", re.I)
ORGAN_TISSUE = {"SI": "small intestine", "SP": "spleen", "LI": "large intestine"}


def spec_for(row: pd.Series, hbm: str, fs: s3fs.S3FileSystem) -> dict | None:
    base = f"{BUCKET}/hubmap/{hbm}"
    keys = fs.find(base)
    fovs = sorted(
        int(m.group(1))
        for k in keys
        for m in [re.search(r"/data/count_matrix_pos(\d+)\.csv$", k)]
        if m
    )
    if not fovs or not any(k.endswith("/data/cell_centroids.csv") for k in keys):
        return None
    meta = json.loads(fs.cat(f"{base}/metadata.json"))
    donor_id, donor = donor_from(meta)
    organ = (meta.get("organs") or [{}])[0].get("organ")
    tissue = ORGAN_TISSUE.get(organ, str(row["tissue"]).lower())
    m = REGION_RE.search(meta.get("description") or "")
    region = m.group(2).lower() if m else None
    gene_list = next((k for k in keys if "/gene-list-seqFISH" in k), None)
    blocks = [s for s in meta.get("samples") or [] if s.get("sample_category") == "block"]
    dapi = {n: f"{base}/segmentation_mask/dapi-hyb0-pos{n}.tif" for n in fovs}
    missing = [n for n in fovs if dapi[n] not in keys]
    if missing:
        print(f"  {hbm}: no DAPI for fov {missing}; those FOVs are skipped")
        fovs = [n for n in fovs if n not in missing]
    files = [
        {"url": f"s3://{base}/data/cell_centroids.csv", "dest": "cell_centroids.csv"},
    ]
    if gene_list:
        files.append({"url": f"s3://{gene_list}", "dest": "gene_list.csv"})
    samples = {}
    for n in fovs:
        files += [
            {"url": f"s3://{base}/data/count_matrix_pos{n}.csv", "dest": f"pos{n}/count_matrix.csv"},
            {"url": f"s3://{dapi[n]}", "dest": f"pos{n}/dapi.tif"},
        ]
        samples[f"{hbm}_fov{n}"] = {
            "section_id": f"{hbm}_fov{n}",
            "fov": n,
            "donor_id": donor_id,
            "block_id": blocks[0]["hubmap_id"] if blocks else None,
            "sample_name": f"{meta.get('title')} -- field of view {n}",
            "anatomical_region": region,
            "disease_state": "unknown",
            "disease": None,
        }
    return {
        "dataset_key": row["dataset_id"],
        "hubmap_id": hbm,
        "uuid": meta.get("uuid"),
        "study": hbm,
        "study_name": meta.get("title"),
        "assay": "seqFISH",
        "technology": "seqfish",
        "spatial_unit": "cell",
        # Cai lab's pipeline segments nuclei on DAPI with a multicut method
        # (segmentation_mask/multicut_*.h5); the schema has no member for that.
        "segmentation_method": "other",
        "organism": "Homo sapiens",
        "tissue": tissue,
        "preservation": "fresh_frozen",  # specimen_preservation_temperature: Liquid Nitrogen
        "image_modality": "dapi",
        "accession_database": "HuBMAP",
        "data_access_link": f"https://portal.hubmapconsortium.org/browse/dataset/{meta.get('uuid')}",
        "download_url": f"s3://{base}/",
        "source": {
            "layout": "hubmap_seqfish",
            "pixel_size_um": PIXEL_SIZE_UM,
            "fovs": fovs,
            "threshold": "data",  # the pipeline also ships data/low_threshold/
            "bytes": int(sum(fs.info(k)["size"] for k in keys if "/HybCycle_" not in k)),
            "published_at": meta.get("published_timestamp"),
            "files": files,
            "notes": (
                "One section per field of view: centroids are FOV-local pixels and the stage "
                "positions in the .pos file do not map onto the posN numbering. DAPI stack "
                "max-projected to a single plane. Segmentation masks and the raw hyb-cycle "
                "stacks are not used."
            ),
        },
        "panel": {
            "panel_name": f"Cai lab seqFISH {tissue} panel (HuBMAP W105)",
            "vendor": "Cai lab, California Institute of Technology",
            "technology": "seqfish",
            "organism": "Homo sapiens",
            "n_targets": None,  # counted from the count matrix (blank rows excluded)
            "has_custom_addon": False,
            "description": "Targeted seqFISH panel of the HuBMAP Caltech TMC; gene_list.csv gives hyb cycle, round and channel per gene.",
        },
        "donors": {donor_id: donor},
        "samples": samples,
    }


def main() -> None:
    fs = s3fs.S3FileSystem(profile=os.environ.get("AWS_PROFILE", "sci-data-dev-poweruser"))
    reg = pd.read_csv(REGISTRY, dtype=str).fillna("")
    rows = reg[reg.dataset_id.str.startswith("hubmap_") & reg.platform.str.contains("seqfish", case=False)]
    os.makedirs("specs/seqfish", exist_ok=True)
    written = 0
    for _, row in rows.iterrows():
        m = re.search(r"hbm(\d{3})_([a-z]{4})_(\d{3})", row["dataset_id"])
        hbm = f"HBM{m.group(1)}.{m.group(2).upper()}.{m.group(3)}"
        spec = spec_for(row, hbm, fs)
        if spec is None:
            print(f"  skip {row['dataset_id']}: no count matrices or centroids staged")
            continue
        path = f"specs/seqfish/{row['dataset_id']}.json"
        json.dump(spec, open(path, "w"), indent=2)
        print(f"wrote {path}: {len(spec['samples'])} FOV section(s), tissue {spec['tissue']}, region {next(iter(spec['samples'].values()))['anatomical_region']}")
        written += 1
    print(f"{written} spec(s)")


if __name__ == "__main__":
    main()
