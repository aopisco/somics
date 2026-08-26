"""Write the collection-level tables and manifest for the Xenium lung package.

Runs after ``build_xenium_lung_package.py`` has laid out the per-sample files.
It writes the four registry tables the schema's FK targets need — donors,
sections, the probe panel, and the section images — and then the
``collection.json`` manifest.

Two things are only knowable at this point, which is why the registries are
written here rather than by hand: ``dataset_uid``, which the ``Dataset`` objects
mint, and the image dimensions, which are read back off the morphology TIFFs.

The panel is written as **one** row for both sections, and that claim is
checked rather than assumed: the two samples' feature tables and gene-panel
designs are compared target for target, and a mismatch is an error. A shared
panel is the reason this pair is worth ingesting together — it is what makes the
healthy and adenocarcinoma sections directly comparable in feature space, which
the NSCLC CosMx data already in the atlas cannot be against either of them.

10x publishes no donor identifiers, ages, or sexes for this release; it states
only that both sections came from Avaden Biosciences and that the collection
spans two donors. Rather than merge them on silence, the two sections get two
donor rows, since a healthy section and an adenocarcinoma section are not
evidence of one person.

The section image is DAPI: ``morphology_focus.ome.tif`` is a single-channel
autofocus projection of the nuclear stain, which the atlas's ``dapi`` modality
maps onto the ``morphology_crop`` pointer.

Run:
    python scripts/assemble_xenium_lung_collection.py
"""

from __future__ import annotations

import json
import os

import pandas as pd
import tifffile
from polycomb.collection import Collection, Dataset, FileTypeTag

# Where the source bundles, packages and atlases live. Defaulted to the
# hackathon box's layout so committed paths still read as they did, and
# overridable so the pipeline can run anywhere else.
DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")

PACKAGE_ROOT = f"{DATA_HOME}/polycomb_data_packages/xenium_lung_preview"
SOURCE_ROOT = f"{DATA_HOME}/datasets/xenium_lung_preview/extracted"
STAGING_ROOT = f"{DATA_HOME}/datasets/xenium_lung_preview/staging"
# The downloaded outs bundles are kept outside the package: 49 GB of vendor zip
# is provenance, not package content, and every file the package needs has been
# extracted out of it.
RAW_ROOT = f"{DATA_HOME}/datasets/xenium_lung_preview/raw"
MANIFEST = "collection.json"

STUDY = "Xenium_Lung_Preview"
PANEL_NAME = "Xenium Human Lung Panel v1 + hLung_100g Add-On"

LANDING = "https://www.10xgenomics.com/datasets/xenium-human-lung-preview-data-1-standard"
BASE_URL = "https://cf.10xgenomics.com/samples/xenium/1.3.0"

# The corpus-side identifier both sections carry in data/st_corpus.csv.
SOURCE_DATASET_ID = "41"
STUDY_NAME = "Human Lung Cancer Preview Data (Xenium Human Lung Gene Expression Panel + add-on)"

SAMPLES = {
    "Xenium_Preview_Human_Non_diseased_Lung_With_Add_on_FFPE": {
        "section": "non_diseased",
        "donor": "non_diseased",
        "sample_name": "Xenium Human Lung 1 - Non-diseased, pre-designed + add-on panel",
        "disease_state": "healthy",
        "disease": None,
        "clinical_diagnosis": None,
        "donor_note": (
            "Non-diseased lung donor of the 10x Human Lung Preview release (tissue sourced from "
            "Avaden Biosciences). 10x publishes no donor identifier, age, or sex, so donor_id is "
            "a package-local key and those columns stay null."
        ),
    },
    "Xenium_Preview_Human_Lung_Cancer_With_Add_on_2_FFPE": {
        "section": "lung_cancer",
        "donor": "lung_cancer",
        "sample_name": "Xenium Human Lung 1 - Lung Cancer, pre-designed + add-on panel",
        "disease_state": "diseased",
        "disease": "lung adenocarcinoma",
        "clinical_diagnosis": "lung adenocarcinoma",
        "donor_note": (
            "Invasive lung adenocarcinoma donor of the 10x Human Lung Preview release (tissue "
            "sourced from Avaden Biosciences). 10x publishes no donor identifier, age, sex, or "
            "tumour stage, so donor_id is a package-local key and those columns stay null."
        ),
    },
}

DESCRIPTION_TEMPLATE = (
    "Adult human lung section, FFPE. The 10x Human Lung Preview release is two sections "
    "profiled on the same panel — one non-diseased and one with invasive adenocarcinoma, both "
    "sourced from Avaden Biosciences across 2 donors; this dataset is the {which} section. "
    "Profiled on Xenium Analyzer with the 292-gene pre-designed Xenium human lung panel "
    "(design PD_339, hLung_292g) plus a 100-gene lung add-on (PD_346, hLung_100g), for 392 "
    "gene targets reported alongside 149 control and blank codewords on a 541-column feature "
    "axis. Xenium Onboard Analysis {sw}; run {run} started {started}. {n_cells} cells, "
    "{median} median transcripts per cell. Cells were segmented by 15 um expansion from the "
    "DAPI nucleus boundary. The outs bundle ships no H&E; the section image ingested here is "
    "the single-channel morphology_focus DAPI projection at {pixel_size} um/px. The "
    "transcript-level table and the multi-z morphology stack are published in the same bundle "
    "but are not part of this package."
)


def dataset_files(
    sample: str, root: str = STAGING_ROOT
) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(root, sample)
    return [
        (os.path.join(d, "cell_feature_matrix.h5"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, "cell_feature_matrix_var.csv"), FileTypeTag.VAR, "gene_expression"),
        (os.path.join(d, "morphology_focus.ome.tif"), FileTypeTag.DATA, "discrete_image"),
        (os.path.join(d, "metrics_summary.csv"), FileTypeTag.OTHER, None),
        (os.path.join(d, "experiment.xenium"), FileTypeTag.OTHER, None),
    ]


def check_panel_is_shared() -> None:
    """Both sections must carry the same feature axis and the same panel design."""
    var_tables = {}
    designs = {}
    for sample in SAMPLES:
        var_tables[sample] = pd.read_csv(
            os.path.join(STAGING_ROOT, sample, "cell_feature_matrix_var.csv")
        )
        with open(os.path.join(SOURCE_ROOT, sample, "gene_panel.json")) as handle:
            payload = json.load(handle)["payload"]
        # Negative-control targets carry a name but no Ensembl id, so the
        # identity is the id where there is one and the control's name
        # otherwise — the same rule the feature axis itself follows.
        designs[sample] = {
            "panel": payload["panel"],
            "targets": sorted(
                (
                    t["type"]["descriptor"],
                    t["type"]["data"].get("id") or t["type"]["data"]["name"],
                )
                for t in payload["targets"]
            ),
        }

    first, second = list(SAMPLES)
    left, right = var_tables[first], var_tables[second]
    if not left.equals(right):
        raise ValueError(
            f"{first} and {second} do not share a feature axis: "
            f"{len(left)} vs {len(right)} features. They cannot share one PanelSchema row, and "
            f"the two sections are not directly comparable in feature space."
        )
    if designs[first] != designs[second]:
        raise ValueError(
            f"{first} and {second} report different gene panel designs; they cannot share one "
            f"PanelSchema row."
        )
    n_genes = int((left.feature_type == "Gene Expression").sum())
    print(
        f"panel check: both sections share {len(left)} features "
        f"({n_genes} genes + {len(left) - n_genes} controls) and one panel design"
    )


def write_registries(uid_by_sample: dict[str, str]) -> None:
    pd.DataFrame(
        [
            {
                "donor_id": f"{STUDY}_{spec['donor']}",
                "organism": "human",
                "sex": "unknown",
                "life_stage": "unknown",
                "human_development_stage": "adult stage",
                "clinical_diagnosis": spec["clinical_diagnosis"],
                "description": spec["donor_note"],
            }
            for spec in SAMPLES.values()
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "donor_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": f"{STUDY}_{spec['section']}",
                "donor_id": f"{STUDY}_{spec['donor']}",
                "tissue": "lung",
                "disease_state": spec["disease_state"],
                "disease": spec["disease"],
                "preservation": "ffpe",
            }
            for spec in SAMPLES.values()
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "tissuesection_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "panel_name": PANEL_NAME,
                "vendor": "10x Genomics",
                "version": "v1 preview",
                "technology": "xenium",
                "organism": "human",
                "n_targets": 392,
                "has_custom_addon": True,
                "description": (
                    "The 292-gene pre-designed Xenium human lung panel (design PD_339, "
                    "hLung_292g) spiked with a 100-gene lung add-on (design PD_346, "
                    "hLung_100g), for 392 gene targets. The measured feature axis is 541 "
                    "columns wide: the 392 targets plus 20 negative-control probes, 41 "
                    "negative-control codewords and 88 unassigned blank codewords. Run on the "
                    "R&D preview configuration of the Xenium Analyzer, so experiment.xenium "
                    'records the panel only as "R&D Panel".'
                ),
            }
        ]
    ).to_csv(os.path.join(STAGING_ROOT, "panel_registry.csv"), index=False)

    rows = []
    for sample, spec in SAMPLES.items():
        path = os.path.join(STAGING_ROOT, sample, "morphology_focus.ome.tif")
        with tifffile.TiffFile(path) as tif:
            level = tif.series[0].levels[0]
            height, width = (int(d) for d in level.shape[:2])
        with open(os.path.join(STAGING_ROOT, sample, "experiment.xenium")) as handle:
            experiment = json.load(handle)
        rows.append(
            {
                "section_id": f"{STUDY}_{spec['section']}",
                "dataset_uid": uid_by_sample[sample],
                "image_modality": "dapi",
                # channel_names and n_z_planes are added during registry
                # harmonization: a list value and a typed null column are not
                # things a CSV round-trips.
                "pixel_size_um": float(experiment["pixel_size"]),
                "height_px": height,
                "width_px": width,
                "is_registered_to_expression": True,
                "source_path": f"{BASE_URL}/{sample}/{sample}_outs.zip",
                "description": (
                    "morphology_focus.ome.tif, the autofocus projection of the DAPI nuclear "
                    "stain at full resolution. Its pixel frame is the same frame the cell "
                    "centroids are reported in — x_px = x_um / pixel_size with no offset — so "
                    "an obs pixel coordinate indexes this image directly."
                ),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(STAGING_ROOT, "sectionimage_registry.csv"), index=False)


def write_dataset_registry() -> None:
    """One row per sample, carrying the provenance the dataset table wants."""
    with open(os.path.join(STAGING_ROOT, "sample_geometry.json")) as handle:
        geometry = {entry["sample"]: entry for entry in json.load(handle)}

    rows = []
    for sample, spec in SAMPLES.items():
        g = geometry[sample]
        rows.append(
            {
                "folder_name": sample,
                "study_name": STUDY_NAME,
                "sample_name": spec["sample_name"],
                "source_dataset_id": SOURCE_DATASET_ID,
                "accession_database": "10x Genomics Datasets",
                "data_access_link": LANDING,
                "download_url": f"{BASE_URL}/{sample}/{sample}_outs.zip",
                "source_path": os.path.join(RAW_ROOT, f"{sample}_outs.zip"),
                "panel_name": PANEL_NAME,
                "dataset_description": DESCRIPTION_TEMPLATE.format(
                    which="non-diseased" if spec["disease"] is None else "adenocarcinoma",
                    sw=g["analysis_sw_version"],
                    run=g["run_name"],
                    started=g["run_start_time"],
                    n_cells=g["n_cells"],
                    median=g["median_transcripts_per_cell"],
                    pixel_size=g["pixel_size_um"],
                ),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(STAGING_ROOT, "dataset_registry.csv"), index=False)


def write_record_metadata() -> None:
    with open(os.path.join(STAGING_ROOT, "sample_geometry.json")) as handle:
        geometry = json.load(handle)
    record = {
        "dataset_id": "10x_xenium_human_lung_preview",
        "title": "Xenium Human Lung Preview Data",
        "source": "10x Genomics (vendor showcase dataset)",
        "landing_page": LANDING,
        "download_base_url": BASE_URL,
        "license": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
        "publication_pmid": None,
        "publication_doi": None,
        "platform": "Xenium Analyzer (R&D preview configuration)",
        "panel": PANEL_NAME,
        "panel_base_design": {"design_id": "PD_339", "name": "hLung_292g", "n_targets": 292},
        "panel_addon_design": {"design_id": "PD_346", "name": "hLung_100g", "n_targets": 100},
        "n_gene_targets": 392,
        "n_feature_axis": 541,
        "tissue_supplier": "Avaden Biosciences",
        "n_donors": 2,
        "samples": geometry,
        "files_not_ingested": {
            "morphology.ome.tif": (
                "The full multi-z morphology stack, 12.6 GB for the non-diseased section alone. "
                "The atlas stores one image per section and morphology_focus.ome.tif is the "
                "vendor's own autofocus projection of it."
            ),
            "morphology_mip.ome.tif": (
                "Maximum-intensity projection of the same stack; morphology_focus is the "
                "sharper of the two projections and only one is ingested."
            ),
            "transcripts.parquet / transcripts.csv.gz": (
                "Per-transcript locations. The atlas has no transcript-level table, so these "
                "are left at the source."
            ),
            "cell_boundaries.parquet / nucleus_boundaries.parquet": (
                "Segmentation polygons. The obs row carries the areas they enclose; the "
                "schema has no polygon field."
            ),
            "analysis.tar.gz / analysis.zarr.zip": (
                "10x graph-based clustering and UMAP. The clusters are unannotated cluster "
                "numbers rather than biological cell types, so no cell_type_original is "
                "carried into the atlas."
            ),
            "*.zarr.zip": (
                "Xenium Explorer viewer copies of data already ingested from the flat files."
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

    check_panel_is_shared()

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
    write_dataset_registry()
    write_record_metadata()

    # gene_panel.json is identical for both sections (checked above), so it
    # travels once, at the collection level.
    shared_panel = os.path.join(STAGING_ROOT, "gene_panel.json")
    if not os.path.exists(shared_panel):
        first = next(iter(SAMPLES))
        with open(os.path.join(SOURCE_ROOT, first, "gene_panel.json")) as src:
            with open(shared_panel, "w") as dst:
                dst.write(src.read())
    collection.add_file(shared_panel, FileTypeTag.LIBRARY)

    for name in (
        "donor_registry.csv",
        "tissuesection_registry.csv",
        "sectionimage_registry.csv",
        "panel_registry.csv",
    ):
        collection.add_file(os.path.join(STAGING_ROOT, name), FileTypeTag.LIBRARY)
    # dataset_registry.csv is not a schema registry table — it is the per-sample
    # provenance the dataset harmonization reads, so it travels as an
    # informational file rather than something to stage into lance_db.
    for name in ("dataset_registry.csv", "record_metadata.json"):
        collection.add_file(os.path.join(STAGING_ROOT, name), FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(PACKAGE_ROOT, MANIFEST)}")
    for sample, dataset in datasets.items():
        print(f"  {sample}: uid={dataset.uid} feature_spaces={dataset.feature_spaces}")


if __name__ == "__main__":
    main()
