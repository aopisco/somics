#!/usr/bin/env python3
"""Write one Visium builder spec per buildable 10x-catalogue Visium/HD dataset.

Input is ``data/tenx_visium_files.csv`` (from ``resolve_tenx_visium_files.py``)
joined to the registry row and the harvested catalogue record. Everything a
spec carries is either measured (file URLs, bytes), taken from 10x's own
metadata (tissue, disease state, preservation, image type), or a controlled
default that follows from the platform (spot vs bin, 55 um vs the 8 um bin).

What is deliberately left null, because 10x does not publish it: donor sex,
age, and development stage. ``blank beats guessed`` -- see CLAUDE.md. The
donor is a package-local key, as in the Xenium preview specs.

Two platform decisions are encoded here rather than in the builder:

- **Visium HD is ingested at the 8 um bin.** ``binned_outputs`` carries 2, 8
  and 16 um; 8 um is the bin 10x's own analyses and Loupe default to, is
  roughly one cell across, and keeps an 11 mm capture area under ~2M obs
  rows. 2 um is available in the same tarball if a later pass wants it.
- ``technology`` is ``visium_hd`` while ``assay`` stays the EFO Visium label:
  EFO does not separate the two instruments; our controlled column does.

Run:
    python scripts/make_tenx_visium_specs.py [--out specs/tenx_visium]
"""

from __future__ import annotations

import argparse
import json
import os
import re

import pandas as pd

FILES = "data/tenx_visium_files.csv"
REGISTRY = "data/datasets.csv"
CATALOG = "data/10x_catalog.json"

ORGANISM = {
    "Human": "Homo sapiens",
    "Mouse": "Mus musculus",
    "Rattus norvegicus": "Rattus norvegicus",
    "Rhesus macaque": "Macaca mulatta",
    "Zebrafish (Danio rerio)": "Danio rerio",
}
HEALTHY = {"healthy", "non-diseased", "normal", "non diseased", "nondiseased"}
PRESERVATION = {"FFPE": "ffpe", "Fresh Frozen": "fresh_frozen", "Fixed Frozen": "fixed_frozen"}
HD_BIN_UM = 8


def preservation_of(notes: str) -> str:
    m = re.search(r"preservation: ([^;]+)", notes or "")
    return PRESERVATION.get(m.group(1).strip(), "unknown") if m else "unknown"


def stains_of(record: dict) -> list[str] | None:
    """Channel names from a title of the form '... Stains: DAPI, Anti-SNAP25, Anti-GFAP'.

    10x's 1.2.0 Targeted / Whole Transcriptome releases were imaged by
    immunofluorescence and say so only here -- there is no 'Image type' line
    and the slug says 'stains', not 'IF'. The names are 10x's own, verbatim.
    """
    m = re.search(r"Stains?:\s*([^.\n]+)", record.get("title") or "")
    if not m:
        return None
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


def image_modality_of(record: dict, slug: str) -> str:
    """H&E unless 10x's own text says the image is fluorescence."""
    body = record.get("body") or ""
    m = re.search(r"Image type:\s*([^\n]+)", body)
    label = (m.group(1) if m else "").lower()
    if any(k in label for k in ("fluoresc", "dapi", " if", "if ", "immuno")):
        return "immunofluorescence"
    if re.search(r"(^|[-_])if([-_]|$)|if_stained|immunofluorescence", slug):
        return "immunofluorescence"
    if stains_of(record):
        return "immunofluorescence"
    return "he"


def disease_of(row: pd.Series, record: dict) -> tuple[str, str | None]:
    """(disease_state, disease) from the registry, falling back to the catalogue."""
    text = row["disease"] if isinstance(row["disease"], str) else None
    if not text:
        names = record.get("diseaseStateNames") or []
        text = names[0] if names else None
    if not text:
        return "unknown", None
    if text.strip().lower() in HEALTHY:
        return "healthy", None
    return "diseased", text.strip()


def development_stage_of(organism: str, record: dict, tissue: str) -> tuple[str, str | None]:
    """(life_stage, human_development_stage), only where the source says so."""
    if "embryo" in tissue.lower():
        return "embryonic", "embryonic stage" if organism == "Homo sapiens" else None
    blob = ((record.get("title") or "") + " " + (record.get("body") or "")).lower()
    if organism == "Homo sapiens" and "adult" in blob:
        return "unknown", "adult stage"
    return "unknown", None


def spec_for(f: pd.Series, row: pd.Series, record: dict) -> dict:
    hd = f["platform"] == "Visium HD"
    slug = row["data_access_link"].rsplit("/", 1)[-1]
    organism = ORGANISM[row["species"]]
    tissue = str(row["tissue"]).strip().lower()
    disease_state, disease = disease_of(row, record)
    life_stage, hds = development_stage_of(organism, record, tissue)
    sample = f["sample"]
    donor_id = f"{sample}_donor"
    rep = re.search(r"[Rr]ep(?:licate)?[_ ]?(\d)", sample)

    files = {"image": f["image_url"]}
    if hd:
        files["binned_outputs"] = f["binned_url"]
    else:
        files["counts"] = f["counts_url"]
        files["spatial"] = f["spatial_url"]

    return {
        "dataset_key": f["dataset_id"],
        "study": slug,
        "study_name": record.get("title") or row["dataset_name"],
        "assay": "Visium Spatial Gene Expression",
        "technology": "visium_hd" if hd else "visium",
        "spatial_unit": "bin" if hd else "spot",
        "unit_size_um": float(HD_BIN_UM if hd else 55),
        **({"hd_bin_um": HD_BIN_UM} if hd else {}),
        "segmentation_method": "grid",
        "organism": row["species"] and organism,
        "tissue": tissue,
        "preservation": preservation_of(row["notes"]),
        "image_modality": image_modality_of(record, slug),
        **({"channel_names": stains_of(record)} if stains_of(record) else {}),
        "accession_database": "10x Genomics Datasets",
        "data_access_link": row["data_access_link"],
        "source": {
            "pipeline": (re.search(r"pipeline: ([^;]+)", row["notes"] or "") or [None, None])[1],
            "product": (re.search(r"product: ([^;]+)", row["notes"] or "") or [None, None])[1],
            "published_at": record.get("publishedAt"),
            "bytes": int(
                f["counts_bytes"] + f["spatial_bytes"] + f["image_bytes"] + f["binned_bytes"]
            ),
        },
        "donors": {
            donor_id: {
                "organism": organism,
                "sex": "unknown",
                "life_stage": life_stage,
                "human_development_stage": hds,
                "clinical_diagnosis": disease,
                "description": (
                    f"Donor of the 10x Genomics dataset '{slug}'. 10x publishes no donor "
                    "identifier, age or sex, so donor_id is a package-local key and those "
                    "columns stay null."
                ),
            }
        },
        "samples": {
            sample: {
                "section_id": sample,
                "donor_id": donor_id,
                "block_id": f"{sample}_block",
                "section_index": 0,
                "replicate": int(rep.group(1)) if rep else 1,
                "accession_id": slug,
                "sample_name": record.get("title") or row["dataset_name"],
                "disease_state": disease_state,
                "disease": disease,
                "files": files,
            }
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="specs/tenx_visium")
    args = ap.parse_args()

    files = pd.read_csv(FILES).fillna({"skip_reason": ""})
    files = files[files.skip_reason == ""]
    reg = pd.read_csv(REGISTRY, low_memory=False).set_index("dataset_id")
    catalog = {r["slug"]: r for r in json.load(open(CATALOG))}

    os.makedirs(args.out, exist_ok=True)
    written = 0
    for _, f in files.iterrows():
        row = reg.loc[f["dataset_id"]]
        slug = row["data_access_link"].rsplit("/", 1)[-1]
        spec = spec_for(f, row, catalog.get(slug, {}))
        with open(os.path.join(args.out, f"{f['dataset_id']}.json"), "w") as handle:
            json.dump(spec, handle, indent=2)
            handle.write("\n")
        written += 1
    print(f"wrote {written} specs to {args.out}/")
    df = pd.DataFrame(
        [json.load(open(os.path.join(args.out, p))) for p in sorted(os.listdir(args.out))]
    )
    print(df.technology.value_counts().to_dict(), df.image_modality.value_counts().to_dict())
    print(df.preservation.value_counts().to_dict())
    print(
        pd.Series([list(s.values())[0]["disease_state"] for s in df.samples])
        .value_counts()
        .to_dict()
    )
    print(
        "missing catalog record:",
        [
            k
            for k in df.dataset_key
            if pd.isna(df.set_index("dataset_key").loc[k, "source"]["published_at"])
        ],
    )


if __name__ == "__main__":
    main()
