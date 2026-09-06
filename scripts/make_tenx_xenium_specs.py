#!/usr/bin/env python3
"""Write one Xenium builder spec per buildable 10x outs bundle.

Input is ``data/tenx_xenium_files.csv`` joined to the registry row and, for
catalogue rows, the harvested catalogue record. The spec shape is the one
``build_xenium_package.py`` / ``assemble_xenium_collection.py`` /
``harmonize_xenium_package.py`` already read for the preview sections, so
nothing downstream changes; what is new is that the values come from a table
rather than a hand-written file.

Decisions encoded here rather than in the builder:

- **section_id is the 10x sample name**, as in the Visium block; the stable
  section uid follows from it, so a re-release under a new version collides on
  purpose and is refused.
- **segmentation_method** is ``cell_boundary_stain`` when the catalogue lists
  "Cell Segmentation Staining" (the multimodal segmentation kit, 2.0+) and
  ``nucleus_expansion`` otherwise -- the two ways Xenium Onboard Analysis draws
  cells. The builder cross-checks against the ``segmentation_method`` column
  where ``cells.parquet`` carries one.
- **image_modality** is ``dapi`` for a single focus image and ``morphology``
  for a channel directory; channel names come from the bundle at build time.
- **panel_name** is what the catalogue text names ("Xenium Human Multi-Tissue
  and Cancer Panel plus 100 custom genes"); ``n_targets`` is left for the
  builder to fill from ``gene_panel.json``. Where no catalogue text names a
  panel, the builder's value from ``gene_panel.json`` is the only source.
- Donor sex, age and development stage stay null: 10x does not publish them.

Run:
    python scripts/make_tenx_xenium_specs.py [--out specs/tenx_xenium]
"""

from __future__ import annotations

import argparse
import json
import os
import re

import pandas as pd

FILES = "data/tenx_xenium_files.csv"
REGISTRY = "data/datasets.csv"
CATALOG = "data/10x_catalog.json"
ORGANISM = {"human": "Homo sapiens", "mouse": "Mus musculus"}
HEALTHY = {
    "healthy",
    "non-diseased",
    "normal",
    "nondiseased",
    "non diseased",
    "wildtype",
    "wild type",
}
PRESERVATION = {"FFPE": "ffpe", "Fresh Frozen": "fresh_frozen", "Fixed Frozen": "fixed_frozen"}
PANEL_RE = re.compile(
    r"Xenium (?:Prime 5K |Human 5K |Human |Mouse )?[A-Z][A-Za-z\- ]{2,60}?(?:Panel|Add-?On)"
    r"(?: (?:plus|with|\+) [^.,;\n\]]{3,50})?"
)


def catalog_record(row: pd.Series, catalog: dict) -> dict:
    slug = str(row["data_access_link"]).rsplit("/", 1)[-1]
    if slug in catalog:
        return catalog[slug]
    sample = re.search(r"/xenium/[^/]+/([^/]+)/", str(row["download_url"]))
    if sample:
        for r in catalog.values():
            if sample.group(1) in (r.get("body") or ""):
                return r
    return {}


def panel_name(record: dict, row: pd.Series) -> str | None:
    text = " ".join([record.get("title") or "", record.get("body") or "", str(row["dataset_name"])])
    text = re.sub(r"\]\([^)]*\)", "", text)  # markdown link targets
    hits = [h.strip() for h in PANEL_RE.findall(text)]
    hits = [h for h in hits if "Panel" in h]
    if not hits:
        return None
    # prefer the longest mention: it carries the add-on / custom-gene clause
    return max(hits, key=len)


def disease_of(row: pd.Series, record: dict) -> tuple[str, str | None]:
    text = row["disease"] if isinstance(row["disease"], str) else None
    if not text:
        names = record.get("diseaseStateNames") or []
        text = names[0] if names else None
    if not text:
        return "unknown", None
    if text.strip().lower() in HEALTHY:
        return "healthy", None
    return "diseased", text.strip()


def preservation_of(row: pd.Series, record: dict) -> str:
    m = re.search(r"preservation: ([^;]+)", str(row["notes"]))
    label = m.group(1).strip() if m else (record.get("preservationMethods") or [None])[0]
    if label in PRESERVATION:
        return PRESERVATION[label]
    blob = (str(row["dataset_name"]) + " " + str(row["download_url"])).lower()
    if "ffpe" in blob:
        return "ffpe"
    if "fresh" in blob or "_ff" in blob:
        return "fresh_frozen"
    return "unknown"


def spec_for(f: pd.Series, row: pd.Series, record: dict) -> dict:
    sample = f["sample"]
    organism = ORGANISM[str(row["species"]).strip().lower()]
    multimodal = "Cell Segmentation Staining" in (record.get("additionalApplications") or []) or (
        "multimodal" in (record.get("title") or "").lower()
    )
    disease_state, disease = disease_of(row, record)
    tissue = str(row["tissue"]).strip().lower()
    slug = (
        str(row["data_access_link"]).rsplit("/", 1)[-1]
        if "10xgenomics.com/datasets" in str(row["data_access_link"])
        else None
    )
    donor_id = f"{sample}_donor"
    return {
        "dataset_key": f["dataset_id"],
        "study": slug or f["dataset_id"],
        "study_name": record.get("title") or str(row["dataset_name"]),
        "assay": "10x Xenium",
        "technology": "xenium",
        "spatial_unit": "cell",
        "segmentation_method": "cell_boundary_stain" if multimodal else "nucleus_expansion",
        "organism": organism,
        "tissue": tissue,
        "preservation": preservation_of(row, record),
        "image_modality": "dapi" if f["morphology_layout"] == "file" else "morphology",
        "accession_database": "10x Genomics Datasets",
        "data_access_link": row["data_access_link"],
        "download_url": f["outs_url"],
        "source": {
            "xoa": f["xoa"],
            "morphology_layout": f["morphology_layout"],
            "protein_codetection": f["protein"] == "yes",
            "bytes": int(f["outs_bytes"]),
            "published_at": record.get("publishedAt"),
        },
        "panel": {
            "panel_name": panel_name(record, row),
            "vendor": "10x Genomics",
            "technology": "xenium",
            "organism": organism,
            "n_targets": None,
            "has_custom_addon": bool(
                re.search(r"add-?on|custom", panel_name(record, row) or "", re.I)
            ),
            "description": None,
        },
        "donors": {
            donor_id: {
                "organism": organism,
                "sex": "unknown",
                "life_stage": "unknown",
                "human_development_stage": None,
                "clinical_diagnosis": disease,
                "description": (
                    f"Donor of the 10x Genomics Xenium dataset '{slug or f['dataset_id']}'. 10x "
                    "publishes no donor identifier, age or sex, so donor_id is a package-local "
                    "key and those columns stay null."
                ),
            }
        },
        "samples": {
            sample: {
                "section_id": sample,
                "donor_id": donor_id,
                "sample_name": record.get("title") or str(row["dataset_name"]),
                "disease_state": disease_state,
                "disease": disease,
            }
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="specs/tenx_xenium")
    ap.add_argument(
        "--include-protein", action="store_true", help="also write the co-detection specs"
    )
    args = ap.parse_args()
    files = pd.read_csv(FILES).fillna({"skip_reason": ""})
    files = files[files.skip_reason == ""]
    if not args.include_protein:
        files = files[files.protein == "no"]
    reg = pd.read_csv(REGISTRY, low_memory=False).set_index("dataset_id")
    catalog = {r["slug"]: r for r in json.load(open(CATALOG))}
    os.makedirs(args.out, exist_ok=True)
    no_panel = []
    for _, f in files.iterrows():
        row = reg.loc[f["dataset_id"]]
        spec = spec_for(f, row, catalog_record(row, catalog))
        if not spec["panel"]["panel_name"]:
            no_panel.append(f["dataset_id"])
        with open(os.path.join(args.out, f"{f['dataset_id']}.json"), "w") as fh:
            json.dump(spec, fh, indent=2)
            fh.write("\n")
    print(f"wrote {len(files)} specs to {args.out}/")
    df = pd.DataFrame(
        [json.load(open(os.path.join(args.out, p))) for p in sorted(os.listdir(args.out))]
    )
    print(
        df.segmentation_method.value_counts().to_dict(),
        df.image_modality.value_counts().to_dict(),
        df.preservation.value_counts().to_dict(),
    )
    print(
        pd.Series([next(iter(s.values()))["disease_state"] for s in df.samples])
        .value_counts()
        .to_dict()
    )
    print(
        f"panel_name from the catalogue text for {len(df) - len(no_panel)}; from gene_panel.json at build time for {len(no_panel)}: {no_panel}"
    )


if __name__ == "__main__":
    main()
