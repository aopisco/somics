#!/usr/bin/env python3
"""Write one Xenium builder spec per staged HuBMAP Xenium dataset.

HuBMAP's Xenium submissions (Stanford TMC, small intestine, Onboard Analysis
3.3) keep the bundle's ``cells.parquet``, ``cell_feature_matrix.h5`` and
``experiment.xenium`` under ``lab_processed/xenium_bundle/``, ``gene_panel.json``
under ``raw/``, and the DAPI z-stack ``lab_processed/images/morphology.ome.tiff``
in place of a focus projection (the builder max-projects it). Everything a
spec needs beyond the bundle -- donor age, sex, race; the section, block and
organ ids; the portal link -- is in the dataset's ``metadata.json``.

Files are S3 URIs; the runner copies them rather than fetching a zip. Section
ids are HuBMAP dataset ids, donors are HuBMAP donor ids (real ids, unlike the
package-local keys 10x's catalogue forces), and ``disease_state`` is
``unknown``: these are organ donors and the record carries a cause of death,
not a diagnosis.

Run:
    python scripts/make_hubmap_xenium_specs.py [--out specs/hubmap_xenium]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re

import pandas as pd
import s3fs

REGISTRY = "data/datasets.csv"
BUCKET = "somics-dev"
ORGANS = {"SI": "small intestine", "LI": "large intestine"}


def life_stage_of(age: float | None) -> str:
    if age is None:
        return "unknown"
    if age < 18:
        return "juvenile"
    if age < 40:
        return "young_adult"
    if age < 65:
        return "middle_aged"
    return "late_adult"


def donor_from(meta: dict) -> tuple[str, dict]:
    donor = meta["donors"][0]
    md = donor.get("metadata")
    md = ast.literal_eval(md) if isinstance(md, str) else (md or {})
    facts = {e["grouping_concept_preferred_term"]: e for e in md.get("organ_donor_data") or []}
    age = float(facts["Age"]["data_value"]) if "Age" in facts else None
    sex = (facts.get("Sex") or {}).get("preferred_term")
    race = (facts.get("Race") or {}).get("preferred_term")
    return donor["hubmap_id"], {
        "organism": "Homo sapiens",
        "sex": sex.lower() if sex in ("Male", "Female") else "unknown",
        "age_value": age,
        "age_unit": "year" if age is not None else None,
        "life_stage": life_stage_of(age),
        "human_development_stage": f"{int(age)}-year-old stage" if age is not None else None,
        "ethnicity": race,
        "clinical_diagnosis": None,
        "description": (
            f"HuBMAP donor {donor['hubmap_id']} ({meta.get('group_name')}). Age, sex and race "
            "as the portal records them; an organ donor, so no diagnosis is recorded."
        ),
    }


def spec_for(row: pd.Series, hbm: str, fs: s3fs.S3FileSystem) -> dict:
    base = f"{BUCKET}/hubmap/{hbm}"
    meta = json.loads(fs.cat(f"{base}/metadata.json"))
    exp = json.loads(fs.cat(f"{base}/lab_processed/xenium_bundle/experiment.xenium"))
    donor_id, donor = donor_from(meta)
    organ = (meta.get("organs") or [{}])[0].get("organ")
    tissue = ORGANS.get(organ, str(row["tissue"]).lower())
    blocks = [s for s in meta.get("samples") or [] if s.get("sample_category") == "block"]
    n_targets = int(exp.get("panel_num_targets_predesigned") or 0) + int(
        exp.get("panel_num_targets_custom") or 0
    )
    stain = exp.get("segmentation_stain") or ""
    return {
        "dataset_key": row["dataset_id"],
        "hubmap_id": hbm,
        "uuid": meta.get("uuid"),
        "study": hbm,
        "study_name": meta.get("title"),
        "assay": "10x Xenium",
        "technology": "xenium",
        "spatial_unit": "cell",
        "segmentation_method": "cell_boundary_stain" if "Stain" in stain else "nucleus_expansion",
        "organism": "Homo sapiens",
        "tissue": tissue,
        "preservation": (exp.get("preservation_method") or "unknown").lower(),
        "image_modality": "dapi",
        "accession_database": "HuBMAP",
        "data_access_link": f"https://portal.hubmapconsortium.org/browse/dataset/{meta.get('uuid')}",
        "download_url": f"s3://{base}/lab_processed/xenium_bundle/",
        "source": {
            "xoa": exp.get("analysis_sw_version"),
            "morphology_layout": "zstack",
            "protein_codetection": False,
            "bytes": int(sum(fs.info(k)["size"] for k in fs.find(base))),
            "published_at": meta.get("published_timestamp"),
            "num_cells": exp.get("num_cells"),
        },
        "panel": {
            "panel_name": exp.get("panel_name"),
            "vendor": "10x Genomics",
            "technology": "xenium",
            "organism": "Homo sapiens",
            "n_targets": n_targets or None,
            "has_custom_addon": bool(exp.get("panel_num_targets_custom")),
            "description": None,
        },
        "donors": {donor_id: donor},
        "samples": {
            hbm: {
                "section_id": hbm,
                "donor_id": donor_id,
                "block_id": blocks[0]["hubmap_id"] if blocks else None,
                "sample_name": meta.get("title"),
                "disease_state": "unknown",
                "disease": None,
                "files": {
                    "cells": f"s3://{base}/lab_processed/xenium_bundle/cells.parquet",
                    "matrix": f"s3://{base}/lab_processed/xenium_bundle/cell_feature_matrix.h5",
                    "experiment": f"s3://{base}/lab_processed/xenium_bundle/experiment.xenium",
                    "gene_panel": f"s3://{base}/raw/gene_panel.json",
                    "zstack": f"s3://{base}/lab_processed/images/morphology.ome.tiff",
                },
            }
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="specs/hubmap_xenium")
    args = ap.parse_args()
    d = pd.read_csv(REGISTRY, low_memory=False)
    rows = d[d.dataset_id.str.startswith("hubmap_") & d.platform.str.contains("Xenium", na=False)]
    fs = s3fs.S3FileSystem()
    os.makedirs(args.out, exist_ok=True)
    n = 0
    for _, row in rows.iterrows():
        m = re.search(r"(HBM[0-9]{3}\.[A-Z]{4}\.[0-9]{3})", str(row["dataset_name"]))
        if not m or not fs.exists(
            f"{BUCKET}/hubmap/{m.group(1)}/lab_processed/xenium_bundle/cells.parquet"
        ):
            print(f"  skip {row['dataset_id']}: no staged xenium_bundle")
            continue
        spec = spec_for(row, m.group(1), fs)
        with open(os.path.join(args.out, f"{row['dataset_id']}.json"), "w") as fh:
            json.dump(spec, fh, indent=2)
            fh.write("\n")
        n += 1
    print(f"wrote {n} specs to {args.out}/")


if __name__ == "__main__":
    main()
