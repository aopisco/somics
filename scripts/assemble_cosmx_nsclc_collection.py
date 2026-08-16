"""Write the collection-level tables and manifest for the CosMx NSCLC package.

Runs after ``build_cosmx_nsclc_package.py`` has laid out the per-sample files.
It writes the four registry tables the schema's FK targets need — donors,
sections, the probe panel, and the section images — and then the
``collection.json`` manifest.

The registries are written here rather than by hand because two of their columns
are only knowable now: ``dataset_uid``, which the ``Dataset`` objects mint, and
the image dimensions, which the stitcher decided.

What the vendor documents about the donors is aggregate — "3 female, 2 male",
"60+", "4 IIIA, 1 IIB" — with no mapping from a statistic to a sample, so the
per-donor columns those would fill stay null and the aggregate is recorded in
each donor's description instead. Inventing an assignment would be worse than
leaving the column empty.

The builder writes into a staging directory and ``coalesce(copy=False)`` moves
everything into the package root — dataset files to ``root/<sample>/``, the
registries to ``root/``, and the informational files to ``root/other_files/``.

Run:
    python scripts/assemble_cosmx_nsclc_collection.py
"""

from __future__ import annotations

import json
import os

import pandas as pd
import tifffile
from polycomb.collection import Collection, Dataset, FileTypeTag

PACKAGE_ROOT = "/home/ubuntu/polycomb_data_packages/cosmx_nsclc_ffpe"
STAGING_ROOT = "/home/ubuntu/datasets/cosmx_nsclc_ffpe/staging"
MANIFEST = "collection.json"

STUDY = "CosMx_NSCLC"
PANEL_NAME = "CosMx Human Universal Cell Characterization RNA Panel (960-plex prototype)"
UM_PER_PX = 0.18

SAMPLES = [
    "Lung5_Rep1",
    "Lung5_Rep2",
    "Lung5_Rep3",
    "Lung6",
    "Lung9_Rep1",
    "Lung9_Rep2",
    "Lung12",
    "Lung13",
]
DONOR_OF = {
    "Lung5_Rep1": ("Lung5", 0),
    "Lung5_Rep2": ("Lung5", 1),
    "Lung5_Rep3": ("Lung5", 2),
    "Lung6": ("Lung6", 0),
    "Lung9_Rep1": ("Lung9", 0),
    "Lung9_Rep2": ("Lung9", 1),
    "Lung12": ("Lung12", 0),
    "Lung13": ("Lung13", 0),
}
DONORS = ["Lung5", "Lung6", "Lung9", "Lung12", "Lung13"]

DONOR_NOTE = (
    "Vendor-reported cohort attributes are aggregate over the five donors and are "
    "not attributed to individual samples: 3 female and 2 male; race White; age 60+; "
    "tumour grade G1-G3; stage IIIA for four donors and IIB for one."
)

BASE_URL = "https://nanostring-public-share.s3.us-west-2.amazonaws.com/SMI-Compressed"
LANDING = (
    "https://brukerspatialbiology.com/products/cosmx-spatial-molecular-imager/"
    "ffpe-dataset/nsclc-ffpe-dataset/"
)

DESCRIPTION = (
    "One FFPE section of non-small-cell lung cancer profiled on a CosMx Spatial "
    "Molecular Imager prototype with the 960-plex CosMx Human Universal Cell "
    "Characterization RNA panel (980 measured targets, of which 20 are negative-control "
    "probes). Cells were segmented from a morphology stain of PanCK, CD45, CD3, DAPI and "
    "a membrane marker; the vendor's flat files report per-cell counts, the segmented "
    "cell area, the mean and maximum fluorescence of each morphology channel within the "
    "cell, and one 5472x3648 px RGB composite image per field of view at 0.18 um/px. The "
    "composites are stitched here into one section image in the same global pixel frame "
    "the cell coordinates use. Released by NanoString (now Bruker Spatial Biology) "
    "alongside He et al. 2022; the raw morphology z-stacks and transcript-level tables "
    "are published separately and are not part of this package."
)


def dataset_files(
    sample: str, root: str = STAGING_ROOT
) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(root, sample)
    return [
        (os.path.join(d, f"{sample}_exprMat_file.csv"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, f"{sample}_var.csv"), FileTypeTag.VAR, "gene_expression"),
        (
            os.path.join(d, f"{sample}_protein_intensity.csv"),
            FileTypeTag.DATA,
            "protein_abundance",
        ),
        (os.path.join(d, f"{sample}_protein_obs.csv"), FileTypeTag.OBS, "protein_abundance"),
        (os.path.join(d, f"{sample}_protein_var.csv"), FileTypeTag.VAR, "protein_abundance"),
        (os.path.join(d, f"{sample}_composite.tif"), FileTypeTag.DATA, "discrete_image"),
        (os.path.join(d, f"{sample}_metadata_file.csv"), FileTypeTag.OTHER, None),
        (os.path.join(d, f"{sample}_fov_positions_file.csv"), FileTypeTag.OTHER, None),
    ]


def write_registries(uid_by_sample: dict[str, str]) -> None:
    pd.DataFrame(
        [
            {
                "donor_id": f"{STUDY}_{donor}",
                "organism": "human",
                "sex": "unknown",
                "life_stage": "late_adult",
                "ethnicity": "White",
                "clinical_diagnosis": "non-small cell lung carcinoma",
                "description": f"NSCLC donor {donor} of the CosMx SMI FFPE release. {DONOR_NOTE}",
            }
            for donor in DONORS
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "donor_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": f"{STUDY}_{sample}",
                "donor_id": f"{STUDY}_{DONOR_OF[sample][0]}",
                "block_id": f"{STUDY}_{DONOR_OF[sample][0]}",
                "section_index": DONOR_OF[sample][1],
                "tissue": "lung",
                "disease_state": "diseased",
                "disease": "non-small cell lung carcinoma",
                "preservation": "ffpe",
            }
            for sample in SAMPLES
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "tissuesection_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "panel_name": PANEL_NAME,
                "vendor": "NanoString Technologies",
                "version": "prototype",
                "technology": "cosmx",
                "organism": "human",
                "n_targets": 960,
                "has_custom_addon": False,
                "description": (
                    "The 960-gene CosMx Human Universal Cell Characterization RNA panel as run "
                    "on the CosMx SMI prototype, reported alongside 20 negative-control probes "
                    "(NegPrb*) for a 980-column feature axis. Includes 100 ligand-receptor pairs."
                ),
            }
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "panel_registry.csv"), index=False)

    rows = []
    for sample in SAMPLES:
        path = os.path.join(STAGING_ROOT, sample, f"{sample}_composite.tif")
        with tifffile.TiffFile(path) as tif:
            height, width = tif.series[0].levels[0].shape[:2]
        rows.append(
            {
                "section_id": f"{STUDY}_{sample}",
                "dataset_uid": uid_by_sample[sample],
                "image_modality": "immunofluorescence",
                "pixel_size_um": UM_PER_PX,
                "height_px": int(height),
                "width_px": int(width),
                "is_registered_to_expression": True,
                "source_path": f"{BASE_URL}/{sample}/{sample}%20SMI%20Flat%20data.tar.gz",
                "description": (
                    "CellComposite fields of view stitched into one section image. The vendor "
                    "renders the morphology channels as RGB: green PanCK, red CD45, grey CD3, "
                    "blue DAPI. Its pixel frame is the flat files' own global frame with the y "
                    "axis flipped to the top-left origin images use, so a cell's obs pixel "
                    "coordinate indexes this image directly."
                ),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(STAGING_ROOT, "sectionimage_registry.csv"), index=False)


def write_record_metadata() -> None:
    with open(os.path.join(STAGING_ROOT, "sample_geometry.json")) as handle:
        geometry = json.load(handle)
    record = {
        "dataset_id": "bruker_cosmx_nsclc_ffpe",
        "title": "CosMx SMI NSCLC FFPE dataset",
        "source": "Bruker Spatial Biology (NanoString Technologies)",
        "landing_page": LANDING,
        "download_base_url": BASE_URL,
        "publication_pmid": 36203011,
        "publication_doi": "10.1038/s41587-022-01483-z",
        "license": "Vendor-released open-source demonstration dataset; no explicit licence stated.",
        "platform": "CosMx Spatial Molecular Imager (prototype instrument)",
        "panel": PANEL_NAME,
        "n_targets": 960,
        "n_negative_control_probes": 20,
        "pixel_size_um": UM_PER_PX,
        "fov_size_px": [3648, 5472],
        "morphology_channels": ["MembraneStain", "PanCK", "CD45", "CD3", "DAPI"],
        "cohort_summary": DONOR_NOTE,
        "samples": geometry,
        "files_not_ingested": {
            "RawMorphologyImages.tar.gz": (
                "361 GB of per-FOV multi-z morphology TIFFs; the rendered CellComposite in the "
                "flat bundle is what the section image is stitched from."
            ),
            "tx_file.csv": (
                "Per-transcript locations, ~3.4 GB per sample. The atlas has no "
                "transcript-level table, so these are left at the source."
            ),
            "All SMI Giotto object.tar.gz": (
                "SMI_Giotto_Object.RData, the paper's analysis object holding its cell-type "
                "calls. It is an S4 Giotto object and needs R to read, so no per-cell cell "
                "type is carried into the atlas."
            ),
        },
    }
    with open(os.path.join(STAGING_ROOT, "record_metadata.json"), "w") as handle:
        json.dump(record, handle, indent=2)


def refresh_manifest() -> None:
    """Register files added to an already-coalesced package.

    Rebuilding from staging is not an option once the package exists — its files
    have been moved out of staging — but the builder can be re-run against the
    package root to add a file it did not emit the first time. Dataset uids are
    read back from the manifest, so nothing already ingested is renumbered.
    """
    manifest = os.path.join(PACKAGE_ROOT, MANIFEST)
    collection = Collection.from_json(manifest)
    added = 0
    for sample in SAMPLES:
        dataset = collection._datasets[sample]
        known = set(dataset.files)
        for path, tag, feature_space in dataset_files(sample, root=PACKAGE_ROOT):
            if path in known:
                continue
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            dataset.add_file(path, tag, feature_space)
            added += 1
            print(f"  + {os.path.relpath(path, PACKAGE_ROOT)} ({tag}, {feature_space})")
    collection.to_json()
    print(f"refreshed {manifest}: {added} file(s) added")


def main() -> None:
    if os.path.exists(os.path.join(PACKAGE_ROOT, MANIFEST)):
        refresh_manifest()
        return

    collection = Collection(root_dir=PACKAGE_ROOT)
    datasets = {}
    for sample in SAMPLES:
        dataset = Dataset(sample)
        for path, tag, feature_space in dataset_files(sample):
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            dataset.add_file(path, tag, feature_space)
        collection.add_dataset(dataset)
        datasets[sample] = dataset

    write_registries({sample: dataset.uid for sample, dataset in datasets.items()})
    write_record_metadata()

    for name in (
        "donor_registry.csv",
        "tissuesection_registry.csv",
        "sectionimage_registry.csv",
        "panel_registry.csv",
    ):
        collection.add_file(os.path.join(STAGING_ROOT, name), FileTypeTag.LIBRARY)
    for name in ("publication.json", "record_metadata.json", "SMI-ReadMe.html"):
        path = os.path.join(STAGING_ROOT, name)
        if os.path.exists(path):
            collection.add_file(path, FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(PACKAGE_ROOT, 'collection.json')}")
    for sample, dataset in datasets.items():
        print(f"  {sample}: uid={dataset.uid} feature_spaces={dataset.feature_spaces}")


if __name__ == "__main__":
    main()
