"""Write the collection-level tables and manifest for the monkman CODEX package.

Runs after ``build_monkman_codex_package.py`` has laid out the per-region files.
It writes the three registry tables the schema's FK targets need — sections, the
antibody panel, and the section images — and then ``collection.json``.

There is no donor registry. The deposit publishes TMA core positions and nothing
else about the patients: no identifiers, no age or sex, no immunotherapy response,
and no mapping from a core to a patient. Since a TMA routinely carries several
cores per patient, minting one donor per core would assert a cohort size the data
does not support, so ``donor_uid`` stays null and the cores are modelled as
sections only.

Registries are written here rather than by hand because two of their columns are
only knowable now: ``dataset_uid``, which the ``Dataset`` objects mint, and the
image dimensions, which the builder decided.

Run:
    python scripts/assemble_monkman_codex_collection.py
"""

from __future__ import annotations

import json
import os
import shutil

import pandas as pd
import tifffile
from polycomb.collection import Collection, Dataset, FileTypeTag

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/monkman_nsclc_codex"
STAGING_ROOT = f"{DATA_HOME}/datasets/monkman_nsclc_codex/staging"
SOURCE_ROOT = f"{DATA_HOME}/datasets/monkman_nsclc_codex/raw"
MANIFEST = "collection.json"

STUDY = "Monkman_NSCLC_CODEX"
PANEL_NAME = "Monkman NSCLC 36-plex CODEX antibody panel"
UM_PER_PX = 0.3775

RECORD_URL = "https://zenodo.org/records/10258578"
DOI = "10.5281/zenodo.10258578"
PUBLICATION_DOI = "10.1186/s12967-024-05035-8"
PMID = 38439077

COMPOSITE_CHANNELS = ["CD45", "PanCK", "DAPI"]

DESCRIPTION = (
    "One core of a non-small-cell lung cancer tissue microarray, imaged on an Akoya CODEX "
    "(PhenoCycler) with a 36-marker antibody panel over 15 staining cycles at 0.3775 um/px. "
    "Cells were segmented in QuPath and the per-cell mean intensity of all 58 imaged channels "
    "- 36 antibody targets plus the per-cycle DAPI counterstain, blank cycles, and unused "
    "channel slots - is carried as the protein readout. Cell types are the authors' own "
    "published labels from Leiden clustering of the 26 markers that passed their QC. The "
    "60-channel uint16 source stack is rendered here as a three-channel CD45/PanCK/DAPI "
    "composite for the crop pointers. Published by Monkman et al. 2024, J Transl Med."
)


def regions() -> list[dict]:
    with open(os.path.join(STAGING_ROOT, "region_geometry.json")) as handle:
        return json.load(handle)


def dataset_files(
    region: str, root: str = STAGING_ROOT
) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(root, region)
    return [
        (
            os.path.join(d, f"{region}_protein_intensity.csv"),
            FileTypeTag.DATA,
            "protein_abundance",
        ),
        (os.path.join(d, f"{region}_obs.csv"), FileTypeTag.OBS, "protein_abundance"),
        (os.path.join(d, f"{region}_var.csv"), FileTypeTag.VAR, "protein_abundance"),
        (os.path.join(d, f"{region}_composite.tif"), FileTypeTag.DATA, "discrete_image"),
        # The QuPath rows this dataset was derived from, with their unrounded
        # intensities: OBS-like, so tagged OTHER per the one-OBS-per-space rule.
        (os.path.join(d, f"{region}_cells.csv"), FileTypeTag.OTHER, None),
    ]


def write_registries(uid_by_region: dict[str, str]) -> None:
    geometry = regions()

    pd.DataFrame(
        [
            {
                "section_id": f"{STUDY}_{entry['tma_core']}",
                "block_id": f"{STUDY}_TMA_s293",
                "slide_id": "s293_c001_v001_r001",
                "tissue": "lung",
                "disease_state": "diseased",
                "disease": "non-small cell lung carcinoma",
                "preservation": "ffpe",
            }
            for entry in geometry
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "tissuesection_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "panel_name": PANEL_NAME,
                "vendor": "Akoya Biosciences",
                "version": "study-specific",
                "technology": "codex",
                "organism": "human",
                "n_targets": 36,
                "has_custom_addon": False,
                "description": (
                    "A study-assembled CODEX panel of 36 oligo-conjugated antibodies imaged over "
                    "15 cycles: CD31, Siglec8, CD4, CD15, CD44, CD107a, CD20, CD38, CD68, CD34, "
                    "CD45RO, CD45, CD141, CD11b, CD11c, PanCK, Podoplanin, CD197, RORgammaT, CD8, "
                    "GranzymeB, CD25, CD21, SPP1, FoxP3, CD56, HLA-DR, Vimentin, Ki67, CD117, "
                    "CD14, CD163, CD183, CD45RA, CD3e and PGP9.5. The feature axis is 58 columns "
                    "wide because the nuclear counterstain is re-imaged every cycle and the blank "
                    "and unused channel slots are reported alongside the targets."
                ),
            }
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "panel_registry.csv"), index=False)

    rows = []
    for entry in geometry:
        region = entry["region"]
        path = os.path.join(STAGING_ROOT, region, f"{region}_composite.tif")
        with tifffile.TiffFile(path) as tif:
            height, width = tif.series[0].levels[0].shape[:2]
        rows.append(
            {
                "section_id": f"{STUDY}_{entry['tma_core']}",
                "dataset_uid": uid_by_region[region],
                "image_modality": "immunofluorescence",
                # channel_names is a list column and is added in harmonization,
                # where it can be written as a list rather than a CSV string.
                "pixel_size_um": UM_PER_PX,
                "height_px": int(height),
                "width_px": int(width),
                "is_registered_to_expression": True,
                "source_path": (f"{RECORD_URL}/files/s293_c001_v001_r001_{region}.ome.tiff"),
                "description": (
                    f"Core {entry['tma_core']} ({region}) of TMA s293. Three planes of the "
                    "60-channel CODEX stack rendered as RGB - red CD45, green PanCK, blue DAPI - "
                    "each stretched from its own 1st to 99.5th percentile into uint8. The frame "
                    "is the source stack's own, so a cell's obs pixel coordinate indexes this "
                    "image directly; the rendering is for display and the raw 16-bit intensities "
                    "are the protein feature space, not these pixels."
                ),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(STAGING_ROOT, "sectionimage_registry.csv"), index=False)


def write_record_metadata() -> None:
    record = {
        "dataset_id": "monkman2024spatial",
        "title": (
            "Spatial Immune Associations of Immunotherapy Response in Non-Small Cell Lung "
            "Cancer by Multiplexed Tissue Imaging"
        ),
        "source": "Zenodo",
        "record_url": RECORD_URL,
        "doi": DOI,
        "license": "CC-BY-4.0",
        "publication_pmid": PMID,
        "publication_doi": PUBLICATION_DOI,
        "platform": "Akoya CODEX / PhenoCycler",
        "panel": PANEL_NAME,
        "n_targets": 36,
        "n_channels": 58,
        "pixel_size_um": UM_PER_PX,
        "image_size_px": [3024, 2688],
        "composite_channels": COMPOSITE_CHANNELS,
        "segmentation": "QuPath cell detection (nucleus detection with cell expansion)",
        "cells_published": 225319,
        "cells_ingested": sum(entry["n_cells"] for entry in regions()),
        "cores_published": 42,
        "cores_ingested": len(regions()),
        "cohort_note": (
            "The deposit publishes TMA core positions only. There is no patient identifier, no "
            "clinical or demographic variable, and no core-to-patient mapping, so no donor rows "
            "are created and the immunotherapy response the paper analyses is not recoverable "
            "from these files."
        ),
        "regions": regions(),
        "files_not_ingested": {
            "s293_c001_v001_r001_reg008/010/011/014/015/020.ome.tiff": (
                "The six cores the authors excluded in QC. Their cells carry no published "
                "cell type, so they are left at the source."
            ),
            "raw 60-channel OME-TIFF planes": (
                "41 GB of uint16 stacks. The per-cell mean intensity of every channel is "
                "carried as the protein feature space and three channels are rendered into "
                "the section composite; the remaining planes stay at Zenodo."
            ),
            "anndata_4301_v4_annotated.h5ad": (
                "The authors' analysis object. Its X is z-scored and its PCA/harmony embeddings "
                "are analysis products, so only its cell-type labels are carried across; the raw "
                "intensities come from 4301_cells.csv."
            ),
        },
    }
    with open(os.path.join(STAGING_ROOT, "record_metadata.json"), "w") as handle:
        json.dump(record, handle, indent=2)


def refresh_manifest() -> None:
    """Register files added to an already-coalesced package."""
    manifest = os.path.join(PACKAGE_ROOT, MANIFEST)
    collection = Collection.from_json(manifest)
    added = 0
    for entry in regions():
        region = entry["region"]
        dataset = collection._datasets[region]
        known = set(dataset.files)
        for path, tag, feature_space in dataset_files(region, root=PACKAGE_ROOT):
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
    for entry in regions():
        region = entry["region"]
        dataset = Dataset(region)
        for path, tag, feature_space in dataset_files(region):
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            dataset.add_file(path, tag, feature_space)
        collection.add_dataset(dataset)
        datasets[region] = dataset

    write_registries({region: dataset.uid for region, dataset in datasets.items()})
    write_record_metadata()

    for name in ("tissuesection_registry.csv", "panel_registry.csv", "sectionimage_registry.csv"):
        collection.add_file(os.path.join(STAGING_ROOT, name), FileTypeTag.LIBRARY)
    # coalesce(copy=False) moves what it is given, so the source file is copied
    # into staging first rather than being taken out of the download directory.
    channels = os.path.join(STAGING_ROOT, "4301_channelnames.csv")
    shutil.copy2(os.path.join(SOURCE_ROOT, "4301_channelnames.csv"), channels)
    collection.add_file(channels, FileTypeTag.OTHER)
    for name in ("record_metadata.json", "publication.json"):
        path = os.path.join(STAGING_ROOT, name)
        if os.path.exists(path):
            collection.add_file(path, FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(PACKAGE_ROOT, MANIFEST)}")
    print(f"  {len(datasets)} datasets, {sum(e['n_cells'] for e in regions())} cells")


if __name__ == "__main__":
    main()
