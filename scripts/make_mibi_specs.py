#!/usr/bin/env python3
"""Write one MIBI builder spec per buildable HuBMAP MIBI dataset.

HuBMAP holds 429 MIBI datasets and all 429 are staged, but they are three
layouts, not one, and only one of them is a per-cell protein dataset a
builder can read on its own:

- **Lab submission** (211, all uterus; 9 files): ``3D_image_stack.ome.tiff``
  (47 channels x 2048 x 2048, int16 ion counts), ``Mapping/cluster_labels_image.tif``
  (the cell segmentation mask, one label per cell), ``extras/antibodies.tsv``
  (37 antibodies with UniProt accessions), ``mcd/channelnames_report.csv``
  (target -> mass), the assay metadata TSV (pixel size, field size), and the
  portal ``metadata.json`` (donor, sample, organ). ``SingleCellData/cells.csv``
  is the *cohort's* table -- 495k cells over 211 fields of view -- and nothing
  in a dataset names its own field, so it cannot be joined; the per-cell
  values are computed from the mask and the stack instead.
- **HuBMAP's re-processing** (172, uterus; ~120 files): ``MIBI [DeepCell + SPRM]``,
  a derivative of a lab-submission dataset (``direct_ancestors``) with the
  SPRM layout the CODEX/PhenoCycler adapter reads. Skipped here and recorded
  as ``sprm_derivative`` on the parent's spec, so whichever adapter ingests a
  section, the other knows to leave it.
- **Bone marrow** (46; 9 files): ``lab_processed/images/*.ome.tiff`` with a
  channel list and no mask or cell table. Image only; not a protein dataset.

Everything a spec carries is measured (file URIs), taken from HuBMAP's own
records (donor age, sex, race; organ; title), or a controlled value that
follows from the platform. Left null on purpose: ``segmentation_method``
(the submission does not say how the mask was made), ``disease`` (nothing in
the record states a diagnosis; ``disease_state`` is ``unknown``), and any
donor field the portal does not carry.

Writes ``specs/mibi/<dataset_id>.json`` and the accounting table
``data/mibi_datasets.csv`` (every MIBI row, buildable or with a skip reason).

Run:
    AWS_PROFILE=sci-data-dev-poweruser python scripts/make_mibi_specs.py [--out specs/mibi]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import json
import os
import subprocess

import pandas as pd

REGISTRY = "data/datasets.csv"
LISTING = "data/.bucket_listing.json"
ACCOUNTING = "data/mibi_datasets.csv"
BUCKET = "s3://somics-dev/hubmap"
PORTAL = "https://portal.hubmapconsortium.org/browse/dataset/"
ASSETS = "https://assets.hubmapconsortium.org"
CACHE = os.path.expanduser("~/.cache/somics/hubmap_metadata")

LAB_FILES = {
    "stack": "3D_image_stack.ome.tiff",
    "mask": "Mapping/cluster_labels_image.tif",
    "antibodies": "extras/antibodies.tsv",
    "channel_report": "mcd/channelnames_report.csv",
    "metadata_json": "metadata.json",
}
ORGAN = {"UT": "uterus", "BM": "bone marrow"}


def s3_ls(prefix: str) -> dict[str, int]:
    out = subprocess.run(
        ["aws", "s3", "ls", f"{BUCKET}/{prefix}/", "--recursive"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    files = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            files[parts[3].split(f"{prefix}/", 1)[1]] = int(parts[2])
    return files


def portal_record(hbm: str) -> dict:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{hbm}.json")
    if not os.path.exists(path):
        subprocess.run(
            ["aws", "s3", "cp", f"{BUCKET}/{hbm}/metadata.json", path, "--only-show-errors"],
            check=True,
        )
    return json.load(open(path))


def layout_of(files: dict[str, int]) -> str:
    names = set(files)
    if "3D_image_stack.ome.tiff" in names and "Mapping/cluster_labels_image.tif" in names:
        return "lab_submission"
    if any(n.startswith("sprm_outputs/") for n in names) or "anndata-zarr" in " ".join(names):
        return "sprm_derivative"
    if any(n.startswith("lab_processed/images/") for n in names):
        return "image_only"
    return "unknown"


def donor_of(record: dict) -> tuple[str, dict]:
    donor = record["donors"][0]
    facts = {}
    for block in ("living_donor_data", "organ_donor_data"):
        for item in donor.get("metadata", {}).get(block, []) or []:
            facts[item.get("grouping_concept_preferred_term")] = (
                item.get("data_value"),
                item.get("units"),
            )
    age = facts.get("Age")
    age_value = float(age[0]) if age and age[0] not in (None, "") else None
    sex = (facts.get("Sex") or ("unknown",))[0].lower()
    if sex not in ("male", "female"):
        sex = "unknown"
    race = (facts.get("Race") or (None,))[0]
    if race and race.strip().lower() in ("unknown", "not reported", ""):
        race = None
    life_stage, hds = "unknown", None
    if age_value is not None:
        # HsapDv terms of the form "N-year-old stage", as the LIBD spec uses; the
        # life-stage bins are the schema enum's, cut at 18, 40 and 65.
        hds = f"{int(age_value)}-year-old stage"
        life_stage = (
            "juvenile"
            if age_value < 18
            else "young_adult"
            if age_value < 40
            else "middle_aged"
            if age_value < 65
            else "late_adult"
        )
    return donor["hubmap_id"], {
        "organism": "Homo sapiens",
        "sex": sex,
        "age_value": age_value,
        "age_unit": "year" if age_value is not None else None,
        "life_stage": life_stage,
        "human_development_stage": hds,
        "ethnicity": race,
        "clinical_diagnosis": None,
        "description": (
            f"HuBMAP donor {donor['hubmap_id']} ({record.get('group_name')}). "
            "Age, sex and race as the portal records them; nothing else is published."
        ),
    }


def panel_of(record: dict, antibodies: list[str]) -> dict:
    digest = hashlib.sha1(",".join(sorted(antibodies)).encode()).hexdigest()[:8]
    group = record.get("group_name")
    return {
        "panel_name": f"HuBMAP {group} MIBI {len(antibodies)}-plex panel {digest}",
        "vendor": "Ionpath",
        "version": "study-specific",
        "technology": "mibi",
        "organism": "Homo sapiens",
        "n_targets": len(antibodies),
        "has_custom_addon": False,
        "description": (
            f"{len(antibodies)} metal-conjugated antibodies imaged on a MIBIscope: "
            + ", ".join(sorted(antibodies))
            + ". The stack also carries the elemental and background channels, "
            "which are flagged is_control on the feature axis."
        ),
    }


def spec_for(
    row: pd.Series, hbm: str, files: dict[str, int], record: dict, derivative: str | None
) -> dict:
    uuid = record["uuid"]
    donor_id, donor = donor_of(record)
    sample = record["samples"][0]["hubmap_id"] if record.get("samples") else None
    organs = [o.get("organ") for o in record.get("organs", []) if o.get("organ")]
    tissue = (
        ORGAN.get(organs[0], str(row["tissue"]).lower()) if organs else str(row["tissue"]).lower()
    )
    description = (
        (record.get("description") or "")
        + " "
        + (record.get("metadata") or {}).get("description", "")
    )
    preservation = "ffpe" if "ffpe" in description.lower() else "unknown"
    tsv = next(n for n in files if n.endswith("-metadata.tsv"))
    src = {k: f"{BUCKET}/{hbm}/{v}" for k, v in LAB_FILES.items()}
    src["metadata_tsv"] = f"{BUCKET}/{hbm}/{tsv}"
    antibodies = record.get("antibodies") or []
    targets = [a.get("channel_id") or a.get("antibody_name") for a in antibodies]
    return {
        "dataset_key": row["dataset_id"],
        "hubmap_id": hbm,
        "hubmap_uuid": uuid,
        "study": f"hubmap_{record.get('group_name', '').replace(' ', '_').lower()}_mibi",
        "study_name": record.get("title") or row["dataset_name"],
        "assay": "MIBI",
        "technology": "mibi",
        "spatial_unit": "cell",
        "segmentation_method": None,
        "organism": "Homo sapiens",
        "tissue": tissue,
        "preservation": preservation,
        # A multiplexed antibody stack acquired by secondary-ion mass spectrometry:
        # not fluorescence, so `immunofluorescence` would misstate the physics, and
        # `other` has no crop pointer to route to. `morphology` is the enum member
        # for a multichannel tissue stack, as the Xenium morphology stack uses.
        "image_modality": "morphology",
        "accession_database": "HuBMAP",
        "data_access_link": PORTAL + uuid,
        "download_url": f"{ASSETS}/{uuid}/{LAB_FILES['stack']}",
        "source": {
            "layout": "lab_submission",
            "bytes": int(sum(files.values())),
            "published_at": record.get("published_timestamp"),
            "sprm_derivative": derivative,
            "cohort_cells_table": (
                f"{BUCKET}/{hbm}/SingleCellData/cells.csv: the cohort-wide table (495k cells, "
                "211 fields); this dataset's field is not identified, so it is not joined"
            ),
        },
        "panel": panel_of(record, targets),
        "donors": {donor_id: donor},
        "samples": {
            hbm: {
                "section_id": hbm,
                "donor_id": donor_id,
                "block_id": sample or f"{hbm}_block",
                "section_index": 0,
                "accession_id": hbm,
                "sample_name": record.get("title") or row["dataset_name"],
                "disease_state": "unknown",
                "disease": None,
                "files": src,
            }
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="specs/mibi")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    d = pd.read_csv(REGISTRY, low_memory=False)
    rows = d[d.dataset_id.str.startswith("hubmap_") & (d.platform == "MIBI")].copy()
    rows["hbm"] = rows.dataset_name.str.extract(r"(HBM[0-9]{3}\.[A-Z]{4}\.[0-9]{3})")[0]
    staged = {k.split("/", 1)[1] for k in json.load(open(LISTING)) if k.startswith("hubmap/")}
    rows = rows[rows.hbm.isin(staged)]
    print(f"{len(rows)} staged MIBI rows; listing prefixes")

    with cf.ThreadPoolExecutor(args.workers) as pool:
        listings = dict(zip(rows.hbm, pool.map(s3_ls, rows.hbm), strict=True))
    layouts = {h: layout_of(f) for h, f in listings.items()}

    # HuBMAP's re-processed copies point back at the submission they derive from.
    derived = [h for h, layout in layouts.items() if layout == "sprm_derivative"]
    with cf.ThreadPoolExecutor(args.workers) as pool:
        records = dict(zip(derived, pool.map(portal_record, derived), strict=True))
    derivative_of = {}
    for h, rec in records.items():
        for anc in rec.get("direct_ancestors", []):
            if anc.get("entity_type") == "Dataset":
                derivative_of[anc["hubmap_id"]] = h

    buildable = [h for h, layout in layouts.items() if layout == "lab_submission"]
    with cf.ThreadPoolExecutor(args.workers) as pool:
        records.update(dict(zip(buildable, pool.map(portal_record, buildable), strict=True)))

    os.makedirs(args.out, exist_ok=True)
    accounting = []
    for _, row in rows.iterrows():
        hbm, layout = row["hbm"], layouts[row["hbm"]]
        entry = {
            "dataset_id": row["dataset_id"],
            "hubmap_id": hbm,
            "tissue": row["tissue"],
            "layout": layout,
            "bytes": int(sum(listings[hbm].values())),
            "files": len(listings[hbm]),
            "sprm_derivative": derivative_of.get(hbm, ""),
            "skip_reason": "",
        }
        if layout == "lab_submission":
            spec = spec_for(row, hbm, listings[hbm], records[hbm], derivative_of.get(hbm))
            with open(os.path.join(args.out, f"{row['dataset_id']}.json"), "w") as handle:
                json.dump(spec, handle, indent=2)
                handle.write("\n")
        elif layout == "sprm_derivative":
            anc = [
                a["hubmap_id"]
                for a in records[hbm].get("direct_ancestors", [])
                if a.get("entity_type") == "Dataset"
            ]
            entry["skip_reason"] = (
                "HuBMAP DeepCell + SPRM re-processing of "
                f"{', '.join(anc) or 'an unstaged dataset'}; SPRM layout, the "
                "CODEX/PhenoCycler adapter's domain, and the same section"
            )
        elif layout == "image_only":
            entry["skip_reason"] = "lab-processed image only: no segmentation mask or cell table"
        else:
            entry["skip_reason"] = f"unrecognised layout: {sorted(listings[hbm])[:4]}"
        accounting.append(entry)

    accounting.sort(key=lambda e: (bool(e["skip_reason"]), e["dataset_id"]))
    with open(ACCOUNTING, "w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=list(accounting[0]))
        w.writeheader()
        w.writerows(accounting)
    n_ok = sum(1 for e in accounting if not e["skip_reason"])
    print(f"wrote {n_ok} specs to {args.out}/ and {ACCOUNTING}")
    print(pd.Series([e["layout"] for e in accounting]).value_counts().to_string())
    print(
        f"{sum(1 for e in accounting if e['layout'] == 'lab_submission' and e['sprm_derivative'])} "
        "submissions have a staged SPRM derivative"
    )


if __name__ == "__main__":
    main()
