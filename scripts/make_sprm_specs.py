#!/usr/bin/env python3
"""Write one SPRM builder spec per staged HuBMAP CODEX / PhenoCycler dataset.

For every registry row on those two platforms whose HuBMAP id (in
``dataset_name``) is staged under ``s3://somics-dev/hubmap/``, list the prefix
and decide whether the Cytokit + SPRM layout is there: an expression pyramid
per region, and SPRM's ``cell_channel_total`` plus a coordinate source (the
AnnData zip or ``cell_centers.csv``) for that region. Rows that qualify get a
spec in ``specs/sprm/``; every row gets a line in ``data/sprm_datasets.csv``
with its verdict, so the accounting of the block is complete.

What a spec carries and where it comes from:

- **Regions** from the expression image stems (``reg1_stitched_expressions``,
  ``reg001_expr``); one sample per region, section_id ``<HBM-ID>_<region>``.
- **Donor** from HuBMAP's own dataset title, which the portal generates from
  the donor record in one fixed form -- "... from the spleen of a 14-year-old
  black or african american female". Age, race and sex are read from that
  with a strict pattern; a title that does not match leaves them null. The
  processed datasets' metadata export carries no donor fields of its own
  (checked: 0 of 274 CODEX/PhenoCycler rows), and the donor entity's
  ``mapped_metadata`` is absent from the staged ``metadata.json``.
  ``life_stage`` follows the bins the LIBD spec used (young_adult at 34,
  middle_aged at 46): under 18 juvenile, 18-44 young_adult, 45-64
  middle_aged, 65 and over late_adult. ``human_development_stage`` is the
  HsapDv "<n>-year-old stage" label.
- **Tissue** from the registry row (HuBMAP's mapped organ), lower-cased for
  the UBERON pass. **Preservation** and **disease** are not published with
  the processed dataset and stay ``unknown`` / null.
- **Block** from ``metadata.json`` samples of category ``block``, where one
  exists.
- **Segmentation** from the pipeline HuBMAP names in ``dataset_type``:
  ``[Cytokit + SPRM]`` segments by U-Net nuclei plus membrane watershed and
  is recorded as ``watershed``; ``[DeepCell + SPRM]`` (the PhenoCycler
  submissions) is Mesmer, which the enum has no member for, so ``other`` with
  the method in the audit trail.

The perturbation rule was applied per dataset: these are untreated donor
tissues from HuBMAP's tissue mapping centers, so no perturbation block is
written.

Run:
    python scripts/make_sprm_specs.py [--out specs/sprm] [--workers 8]
        [--listing data/.bucket_listing.json] [--cache /tmp/sprm_meta]
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import csv
import json
import os
import re
import subprocess

import pandas as pd

REGISTRY = "data/datasets.csv"
BUCKET = "s3://somics-dev/hubmap"
PORTAL = "https://portal.hubmapconsortium.org/browse/dataset"
TECHNOLOGY = {"CODEX": "codex", "PhenoCycler": "phenocycler"}
TITLE_RE = re.compile(
    r"of an? (?P<age>\d+)-(?P<unit>year|month)-old (?P<race>.+?) (?P<sex>male|female)$"
)
EXPR_RE = re.compile(
    r"^ometiff-pyramids/(?:stitched/expressions|pipeline_output/expr)/(?P<stem>[^/]+?)\.ome\.tiff?$"
)


def life_stage_of(age: float, unit: str) -> str:
    years = age if unit == "year" else age / 12.0
    if years < 18:
        return "juvenile"
    if years < 45:
        return "young_adult"
    if years < 65:
        return "middle_aged"
    return "late_adult"


def donor_from_title(title: str) -> dict:
    m = TITLE_RE.search(title or "")
    if not m:
        return {
            "sex": "unknown",
            "age_value": None,
            "age_unit": None,
            "life_stage": "unknown",
            "human_development_stage": None,
            "ethnicity": None,
        }
    age, unit = float(m["age"]), m["unit"]
    return {
        "sex": m["sex"],
        "age_value": age,
        "age_unit": unit,
        "life_stage": life_stage_of(age, unit),
        "human_development_stage": f"{int(age)}-{unit}-old stage",
        "ethnicity": None if m["race"].lower() == "unknown" else m["race"],
    }


def s3_listing(hbm: str) -> list[str]:
    out = subprocess.run(
        ["aws", "s3", "ls", f"{BUCKET}/{hbm}/", "--recursive"], capture_output=True, text=True
    ).stdout
    return [line.split()[-1].split(f"{hbm}/", 1)[1] for line in out.splitlines() if line.strip()]


def fetch_metadata(hbm: str, cache: str) -> dict:
    path = os.path.join(cache, f"{hbm}.metadata.json")
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        subprocess.run(
            ["aws", "s3", "cp", f"{BUCKET}/{hbm}/metadata.json", path, "--only-show-errors"],
            check=False,
        )
    if not os.path.exists(path):
        return {}
    return json.load(open(path))


def _listish(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
    return value or []


def regions_of(keys: list[str]) -> dict[str, dict]:
    """region -> the relative keys the builder needs, where all of them exist."""
    regions: dict[str, dict] = {}
    for key in keys:
        m = EXPR_RE.match(key)
        if not m:
            continue
        stem = m["stem"]
        region = stem.split("_")[0]
        files = {"expression_image": key}
        for k in keys:
            if not k.startswith("sprm_outputs/") or not k.startswith(f"sprm_outputs/{stem}"):
                continue
            if k.endswith("-cell_channel_total.csv"):
                files["cell_channel_total"] = k
            elif k.endswith("-cell_centers.csv"):
                files["cell_centers"] = k
        for k in keys:
            if k.startswith("anndata-zarr/") and k.endswith(".zarr.zip") and stem in k:
                files["anndata"] = k
        if "experiment.yaml" in keys:
            files["experiment_yaml"] = "experiment.yaml"
        regions[region] = files
    return regions


def build_spec(row: pd.Series, hbm: str, keys: list[str], meta: dict) -> tuple[dict | None, str]:
    regions = regions_of(keys)
    if not regions:
        return None, "no expression pyramid under ometiff-pyramids/"
    incomplete = [
        r
        for r, f in regions.items()
        if "cell_channel_total" not in f or not ({"anndata", "cell_centers"} & set(f))
    ]
    if incomplete:
        return None, f"regions without SPRM totals or centroids: {incomplete}"
    if not meta:
        return None, "metadata.json not staged"
    tissue = str(row["tissue"]).strip().lower()
    if "," in tissue or tissue in ("nan", ""):
        return None, f"tissue {row['tissue']!r} is not one organ"

    uuid = meta["uuid"]
    title = meta.get("title") or row["dataset_name"]
    donor = donor_from_title(title)
    donors = _listish(meta.get("donors"))
    hubmap_donor = donors[0].get("hubmap_id") if donors else None
    blocks = [s for s in _listish(meta.get("samples")) if s.get("sample_category") == "block"]
    donor_id = f"{hbm}_donor"
    technology = TECHNOLOGY[row["platform"]]
    pipeline = re.search(r"\[([^\]]+)\]", meta.get("dataset_type") or "")
    pipeline = pipeline.group(1) if pipeline else "Cytokit + SPRM"
    if "deepcell" in pipeline.lower():
        segmentation, note = (
            "other",
            "DeepCell (Mesmer) whole-cell segmentation on the nuclear and membrane channels; "
            "the enum has no deep-learning member other than cellpose, which this is not",
        )
    else:
        segmentation, note = (
            "watershed",
            "Cytokit detects nuclei with a U-Net on the nuclear channel and grows cell "
            "boundaries by marker-controlled watershed on the membrane channel",
        )

    spec = {
        "dataset_key": row["dataset_id"],
        "hubmap_id": hbm,
        "uuid": uuid,
        "study": uuid,
        "study_name": title,
        "assay": "PhenoCycler",
        "technology": technology,
        "spatial_unit": "cell",
        "segmentation_method": segmentation,
        "segmentation_note": note,
        "pipeline": pipeline,
        "organism": "Homo sapiens",
        "tissue": tissue,
        "preservation": "unknown",
        "image_modality": "immunofluorescence",
        "accession_database": "HuBMAP",
        "accession_id": hbm,
        "data_access_link": f"{PORTAL}/{uuid}",
        "s3_prefix": f"{BUCKET}/{hbm}",
        "source": {
            "dataset_type": meta.get("dataset_type"),
            "group_name": meta.get("group_name"),
            "published_timestamp": meta.get("published_timestamp"),
            "n_regions": len(regions),
        },
        "donors": {
            donor_id: {
                "organism": "Homo sapiens",
                **donor,
                "clinical_diagnosis": None,
                "description": (
                    f"HuBMAP donor {hubmap_donor or 'unknown'} "
                    f"({meta.get('group_name', 'HuBMAP')}). "
                    + (
                        "Sex, age and race are read from the portal's dataset title; "
                        if donor["sex"] != "unknown"
                        else "The dataset title names no age, sex or race; "
                    )
                    + "no clinical history is published with the processed dataset. donor_id is "
                    "package-local."
                ),
            }
        },
        "samples": {
            region: {
                "section_id": f"{hbm}_{region}",
                "donor_id": donor_id,
                "block_id": blocks[0]["hubmap_id"] if blocks else None,
                "section_index": i,
                "sample_name": f"{hbm} region {region}",
                "disease_state": "unknown",
                "disease": None,
                "files": files,
            }
            for i, (region, files) in enumerate(sorted(regions.items()))
        },
    }
    return spec, ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="specs/sprm")
    ap.add_argument("--listing", default="data/.bucket_listing.json")
    ap.add_argument("--cache", default="/tmp/sprm_meta")
    ap.add_argument("--report", default="data/sprm_datasets.csv")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    staged = {k.split("/", 1)[1] for k in json.load(open(args.listing)) if k.startswith("hubmap/")}
    d = pd.read_csv(REGISTRY, low_memory=False)
    h = d[d.dataset_id.str.startswith("hubmap_") & d.platform.isin(TECHNOLOGY)].copy()
    h["hbm"] = h.dataset_name.str.extract(r"(HBM[0-9]{3}\.[A-Z]{4}\.[0-9]{3})")[0]
    rows = [r for _, r in h.iterrows() if r.hbm in staged]
    print(f"{len(h)} registry rows on CODEX/PhenoCycler; {len(rows)} staged")

    def work(r):
        keys = s3_listing(r.hbm)
        meta = fetch_metadata(r.hbm, args.cache)
        spec, why = build_spec(r, r.hbm, keys, meta)
        return r, spec, why, keys

    os.makedirs(args.out, exist_ok=True)
    report = []
    with cf.ThreadPoolExecutor(args.workers) as pool:
        for r, spec, why, keys in pool.map(work, rows):
            if spec:
                with open(os.path.join(args.out, f"{r.dataset_id}.json"), "w") as handle:
                    json.dump(spec, handle, indent=2)
                    handle.write("\n")
            n_regions = len(spec["samples"]) if spec else 0
            donor = next(iter(spec["donors"].values())) if spec else {}
            report.append(
                {
                    "dataset_id": r.dataset_id,
                    "hubmap_id": r.hbm,
                    "platform": r.platform,
                    "tissue": r.tissue,
                    "n_files": len(keys),
                    "n_regions": n_regions,
                    "donor_sex": donor.get("sex", ""),
                    "donor_age": donor.get("age_value", ""),
                    "spec": f"{args.out}/{r.dataset_id}.json" if spec else "",
                    "skip_reason": why,
                }
            )
    report.sort(key=lambda x: (bool(x["skip_reason"]), x["platform"], x["dataset_id"]))
    with open(args.report, "w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=list(report[0]))
        w.writeheader()
        w.writerows(report)
    ok = [x for x in report if not x["skip_reason"]]
    print(f"wrote {len(ok)} specs to {args.out}/; {len(report) - len(ok)} skipped -> {args.report}")
    print(pd.DataFrame(ok).platform.value_counts().to_dict())
    print("regions per dataset:", pd.Series([x["n_regions"] for x in ok]).value_counts().to_dict())
    print("donor sex parsed:", pd.DataFrame(ok).donor_sex.value_counts().to_dict())
    for x in report:
        if x["skip_reason"]:
            print(f"  skip {x['dataset_id']}: {x['skip_reason']}")


if __name__ == "__main__":
    main()
