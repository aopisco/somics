"""Reconcile four registry rows whose download_url contradicted their metadata.

Each fix is anchored to a stated source, not inferred from a name:
PMC11645551's Table 1 for the two rows that came from it, the row's own
`notes` for the prostate row, and the 10x release family for the breast row.
Every URL below returned HTTP 200 when probed.
"""

import csv
import os
import sys

FIX = {
    "hbc_xenium": {
        "data_access_link": "https://www.10xgenomics.com/products/xenium-in-situ/preview-dataset-human-breast",
        "download_url": "",
        "notes": (
            "PMC11645551 Table 1: HBC = Xenium human breast preview, 3 sections "
            "(167,780 / 118,752 / 142,272 cells; 313 genes S1, 288 genes S2). "
            "One row cannot carry three bundles, so download_url is left blank; the "
            "verified bundles are Xenium_V1_FFPE_Human_Breast_IDC_With_Addon (1.0.2 and "
            "1.3.0) and Xenium_V1_FFPE_Human_Breast_ILC (1.0.2). "
            "Previously pointed at Xenium_V1_human_Pancreas_FFPE, which is the same "
            "paper's HP dataset, not this one."
        ),
    },
    "hbchd_visiumhd": {
        "data_access_link": "https://www.10xgenomics.com/datasets/visium-hd-cytassist-gene-expression-mouse-brain-fresh-frozen",
        "download_url": "https://cf.10xgenomics.com/samples/spatial-exp/3.1.1/Visium_HD_Mouse_Brain_Fresh_Frozen/"
        "Visium_HD_Mouse_Brain_Fresh_Frozen_binned_outputs.tar.gz",
        "notes": (
            "This row is PMC11645551's MBHD (mouse brain, Visium HD), which is what its "
            "species and tissue say; the dataset_id is a misnomer. HBCHD is a different "
            "dataset in the same paper (human breast cancer, Visium HD) and is not yet in "
            "this registry. Previously pointed at the HBCHD bundle."
        ),
    },
    "jiang2024_visium": {
        "data_access_link": "https://www.10xgenomics.com/cn/datasets/human-prostate-cancer-adenocarcinoma-with-invasive-carcinoma-ffpe-1-standard-1-3-0",
        "download_url": "https://cf.10xgenomics.com/samples/spatial-exp/1.3.0/Visium_FFPE_Human_Prostate_Cancer/"
        "Visium_FFPE_Human_Prostate_Cancer_filtered_feature_bc_matrix.tar.gz",
        "notes": (
            "Corrected from Visium_FFPE_Human_Ovarian_Cancer. The prostate URL was already "
            "recorded in this row's own notes, which is what the dataset_name states."
        ),
    },
    "breast_cancer_br2024_visium": {
        "data_access_link": "https://www.10xgenomics.com/resources/datasets/human-breast-cancer-whole-transcriptome-analysis-1-standard-1-2-0",
        "download_url": "https://cf.10xgenomics.com/samples/spatial-exp/1.2.0/Parent_Visium_Human_BreastCancer/"
        "Parent_Visium_Human_BreastCancer_filtered_feature_bc_matrix.tar.gz",
        "notes": (
            "Corrected from Parent_Visium_Human_OvarianCancer. Inferred from the 10x release "
            "family rather than stated by the source: the wrong link was the 1.2.0 'Parent_' "
            "ovarian bundle, and this is its breast counterpart in the same release. Probed 200."
        ),
    },
}

path = "data/datasets.csv"
rows = list(csv.DictReader(open(path)))
cols = list(rows[0])
changed = 0
for r in rows:
    fix = FIX.get(r["dataset_id"])
    if not fix:
        continue
    print(f"\n{r['dataset_id']}  ({r['species']}, {r['tissue']})")
    for k, v in fix.items():
        old = r.get(k) or ""
        if k == "notes":
            r[k] = v
            print("  notes            <- rewritten")
            continue
        print(f"  {k:16} {old[:64] or '(blank)'}\n  {'':16} -> {v[:64] or '(blank)'}")
        r[k] = v
    changed += 1

if "--apply" not in sys.argv:
    print(f"\n{changed} row(s) would change. Pass --apply to write.")
    raise SystemExit
tmp = path + ".tmp"
with open(tmp, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, restval="")
    w.writeheader()
    w.writerows(rows)
os.replace(tmp, path)
print(f"\nwrote {path} ({changed} rows changed)")
