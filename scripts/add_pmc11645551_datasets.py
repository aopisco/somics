#!/usr/bin/env python3
"""Add the four datasets PMC11645551 uses that extraction never captured.

The paper's Table 1 lists seven 10x datasets. Five became registry rows; HP,
HN, MBS and HBCHD did not, and two of their URLs were left attached to rows
they do not belong to (see scripts/reconcile_10x_links.py).

Every field below is taken from that table -- species, tissue, section count,
cell/spot count, panel size and landing page -- rather than inferred. The
download URLs were probed and returned 200.

This also renames the existing ``hbchd_visiumhd`` to ``mbhd_visiumhd``. That row
is the paper's MBHD (mouse brain), which is what its species and tissue say; the
id was simply wrong, and the name is needed for the real HBCHD being added here.
Safe to rename: nothing has been ingested under these ids.

Run:
    python scripts/add_pmc11645551_datasets.py [--apply]
"""

from __future__ import annotations

import csv
import os
import sys

PAPER = "PMC11645551"
CDN = "https://cf.10xgenomics.com/samples"
PAGE = "https://www.10xgenomics.com/datasets"


def xen(version: str, sample: str, suffix: str = "outs") -> str:
    return f"{CDN}/xenium/{version}/{sample}/{sample}_{suffix}.zip"


def vis(version: str, sample: str, artifact: str) -> str:
    return f"{CDN}/spatial-exp/{version}/{sample}/{sample}_{artifact}.tar.gz"


NEW = [
    {
        "dataset_id": "hp_xenium",
        "dataset_name": "HP (human pancreas, Xenium multimodal cell segmentation)",
        "platform": "Xenium",
        "modality": "spatial transcriptomics",
        "is_spatial": "yes",
        "species": "Human",
        "tissue": "Pancreas",
        "n_samples": "1",
        "data_access_link": f"{PAGE}/ffpe-human-pancreas-with-xenium"
        "-multimodal-cell-segmentation-1-standard",
        "download_url": xen("2.0.0", "Xenium_V1_human_Pancreas_FFPE", "xe_outs"),
        "notes": f"{PAPER} Table 1: 140,702 cells, 377 genes. 10x vendor dataset.",
    },
    {
        "dataset_id": "hn_xenium",
        "dataset_name": "HN (human heart, non-diseased, Xenium multi-tissue and cancer panel)",
        "platform": "Xenium",
        "modality": "spatial transcriptomics",
        "is_spatial": "yes",
        "species": "Human",
        "tissue": "Heart",
        "n_samples": "1",
        "data_access_link": f"{PAGE}/human-heart-data-xenium-human"
        "-multi-tissue-and-cancer-panel-1-standard",
        "download_url": xen("1.9.0", "Xenium_V1_hHeart_nondiseased_section_FFPE"),
        "notes": f"{PAPER} Table 1: 26,366 cells, 377 genes. 10x vendor dataset.",
    },
    {
        "dataset_id": "mbs_visium",
        "dataset_name": "MBS (mouse brain, sagittal posterior, serial section 2)",
        "platform": "Visium",
        "modality": "spatial transcriptomics",
        "is_spatial": "yes",
        "species": "Mouse",
        "tissue": "Brain (sagittal)",
        "n_samples": "1",
        "data_access_link": f"{PAGE}/mouse-brain-serial-section-2-sagittal-posterior-1-standard",
        "download_url": vis(
            "1.1.0", "V1_Mouse_Brain_Sagittal_Posterior_Section_2", "filtered_feature_bc_matrix"
        ),
        "notes": f"{PAPER} Table 1: 3,289 spots at 55 um, 32,285 genes. 10x vendor dataset.",
    },
    {
        "dataset_id": "hbchd_visiumhd",
        "dataset_name": "HBCHD (human breast cancer, Visium HD, fresh frozen)",
        "platform": "Visium HD",
        "modality": "spatial transcriptomics",
        "is_spatial": "yes",
        "species": "Human",
        "tissue": "Breast cancer",
        "n_samples": "1",
        "data_access_link": f"{PAGE}/visium-hd-cytassist-gene-expression"
        "-human-breast-cancer-fresh-frozen",
        "download_url": vis(
            "3.1.1", "Visium_HD_Human_Breast_Cancer_Fresh_Frozen", "binned_outputs"
        ),
        "notes": f"{PAPER} Table 1: 472,859 squares at 8 um, 17,527 genes. 10x vendor dataset.",
    },
]

# Vendor datasets carry the vendor as their reference, per the registry's rule.
COMMON = {
    "disease": "",
    "perturbation": "",
    "data_downloadable": "",
    "original_publication": "10x Genomics Datasets",
    "original_publication_link": "",
    "original_publication_year": "",
    "first_published_by_model_paper": "no",
    "candidate_accessions": "",
}
USAGE = {"hp_xenium": "HP", "hn_xenium": "HN", "mbs_visium": "MBS", "hbchd_visiumhd": "HBCHD"}


def main() -> None:
    apply = "--apply" in sys.argv
    ds_path, use_path = "data/datasets.csv", "data/model_dataset_usage.csv"
    rows = list(csv.DictReader(open(ds_path)))
    cols = list(rows[0])

    # The existing hbchd_visiumhd is MBHD; free the name before adding the real one.
    renamed = 0
    for r in rows:
        if r["dataset_id"] == "hbchd_visiumhd":
            r["dataset_id"] = "mbhd_visiumhd"
            r["dataset_name"] = "MBHD (mouse brain, Visium HD, fresh frozen)"
            renamed += 1
    print(f"renamed hbchd_visiumhd -> mbhd_visiumhd: {renamed} row(s)")

    have = {r["dataset_id"] for r in rows}
    added = []
    for spec in NEW:
        if spec["dataset_id"] in have:
            print(f"  already present, skipped: {spec['dataset_id']}")
            continue
        row = {c: "" for c in cols}
        row.update(COMMON)
        row.update({k: v for k, v in spec.items() if k in cols})
        added.append(row)
        print(
            f"  + {row['dataset_id']:<16} {row['species']:<6} {row['tissue']:<18} {row['platform']}"
        )

    use = list(csv.DictReader(open(use_path)))
    ucols = list(use[0])
    template = next((u for u in use if u["dataset_id"] == "hbc_xenium"), None)
    seen = {(u["model_paper_link"], u["dataset_id"]) for u in use}
    for u in use:
        if u["dataset_id"] == "hbchd_visiumhd":
            u["dataset_id"] = "mbhd_visiumhd"
            u["alias_in_model_paper"] = "MBHD"
    new_use = []
    for did, alias in USAGE.items():
        if did == "hbchd_visiumhd" and (template["model_paper_link"], did) in seen:
            pass
        row = {c: "" for c in ucols}
        row.update(
            {
                "model": template["model"],
                "model_paper_title": template.get("model_paper_title", ""),
                "model_paper_link": template["model_paper_link"],
                "dataset_id": did,
                "usage": template.get("usage", "analysis/benchmark"),
                "alias_in_model_paper": alias,
            }
        )
        new_use.append(row)
    print(f"usage rows to add: {len(new_use)}")

    if not apply:
        print("\ndry run — pass --apply to write")
        return
    for path, cs, data in ((ds_path, cols, rows + added), (use_path, ucols, use + new_use)):
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cs, restval="")
            w.writeheader()
            w.writerows(data)
        os.replace(tmp, path)
    print(f"wrote {ds_path} (+{len(added)}) and {use_path} (+{len(new_use)})")


if __name__ == "__main__":
    main()
