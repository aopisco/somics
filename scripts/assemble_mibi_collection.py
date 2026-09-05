#!/usr/bin/env python3
"""Write the registries and collection manifest for a MIBI package, spec-driven.

The counterpart to ``build_mibi_package.py``: everything that varies by dataset
-- the donor, the section, the antibody panel, the image geometry -- comes from
the spec and from what the builder measured; everything structural is fixed.

Four registries: donors, tissue sections, the panel, and section images. The
panel is real here (a targeted antibody set), unlike Visium, and every obs row
joins to it. ``channel_names`` is a list column and is written in
harmonization, where it can be a list rather than a CSV string.

``section_id`` is the dataset's HuBMAP id and ``donor_id`` the donor's HuBMAP
id, verbatim: their uids are content hashes of these strings, so two fields of
view from one donor share a donor row in the atlas, and a re-run reproduces
the same section.

Run:
    python scripts/assemble_mibi_collection.py --spec specs/mibi/<dataset>.json
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd
from polycomb.collection import Collection, Dataset, FileTypeTag

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
MANIFEST = "collection.json"


def dataset_files(g: dict, staging: str) -> list[tuple[str, FileTypeTag, str | None]]:
    sample = g["sample"]
    d = os.path.join(staging, sample)
    return [
        (os.path.join(d, f"{sample}_protein_counts.csv"), FileTypeTag.DATA, "protein_abundance"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "protein_abundance"),
        (os.path.join(d, f"{sample}_var.csv"), FileTypeTag.VAR, "protein_abundance"),
        (os.path.join(d, g["image_file"]), FileTypeTag.DATA, "discrete_image"),
        # The submission's own descriptions of the acquisition and the antibodies.
        (os.path.join(d, "assay_metadata.tsv"), FileTypeTag.OTHER, None),
        (os.path.join(d, "antibodies.tsv"), FileTypeTag.OTHER, None),
        (os.path.join(d, "channelnames_report.csv"), FileTypeTag.OTHER, None),
    ]


def write_registries(
    spec: dict, uid_by_sample: dict[str, str], geometry: list[dict], staging: str
) -> None:
    pd.DataFrame(
        [
            {
                "donor_id": donor_id,
                "DonorSchema_join": donor_id,
                "organism": donor.get("organism", spec["organism"]),
                "sex": donor["sex"],
                # Written through the CSV so an all-null column stays a plain
                # string Lance can write (see the Xenium assembler).
                "age_value": donor.get("age_value"),
                "age_unit": donor.get("age_unit"),
                "life_stage": donor["life_stage"],
                "human_development_stage": donor.get("human_development_stage"),
                "ethnicity": donor.get("ethnicity"),
                "clinical_diagnosis": donor.get("clinical_diagnosis"),
                "description": donor.get("description"),
            }
            for donor_id, donor in spec["donors"].items()
        ]
    ).to_csv(os.path.join(staging, "donor_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": entry["section_id"],
                "TissueSectionSchema_join": entry["section_id"],
                "donor_id": entry["donor_id"],
                "donor_uid_DonorSchema_join": entry["donor_id"],
                "block_id": entry["block_id"],
                "section_index": entry.get("section_index", 0),
                "tissue": entry.get("tissue", spec["tissue"]),
                "disease_state": entry.get("disease_state", "unknown"),
                # Present and null: the resolution pass plans a disease pass from
                # the schema and errors if the column is absent.
                "disease": entry.get("disease"),
                "preservation": spec["preservation"],
            }
            for entry in spec["samples"].values()
        ]
    ).to_csv(os.path.join(staging, "tissuesection_registry.csv"), index=False)

    panel = spec["panel"]
    pd.DataFrame([{**panel, "PanelSchema_join": panel["panel_name"]}]).to_csv(
        os.path.join(staging, "panel_registry.csv"), index=False
    )

    pd.DataFrame(
        [
            {
                "section_id": spec["samples"][g["sample"]]["section_id"],
                "dataset_uid": uid_by_sample[g["sample"]],
                "section_uid_TissueSectionSchema_join": spec["samples"][g["sample"]]["section_id"],
                "image_modality": spec["image_modality"],
                "pixel_size_um": g["pixel_size_um"],
                "height_px": g["height_px"],
                "width_px": g["width_px"],
                "is_registered_to_expression": True,
                "source_path": g["image_source"],
                "description": (
                    f"The full {g['n_channels']}-channel MIBI ion-count stack of the field of "
                    "view, one plane per metal tag, stored channels-last as uint16. The "
                    "segmentation mask the obs rows derive from is in this frame, so a cell's "
                    "pixel centroid indexes this image directly."
                ),
            }
            for g in geometry
        ]
    ).to_csv(os.path.join(staging, "sectionimage_registry.csv"), index=False)


def write_dataset_registry(spec: dict, geometry: list[dict], staging: str) -> None:
    pd.DataFrame(
        [
            {
                "folder_name": g["sample"],
                "study_name": spec["study_name"],
                "sample_name": spec["samples"][g["sample"]].get("sample_name", g["sample"]),
                "source_dataset_id": spec["dataset_key"],
                "accession_database": spec["accession_database"],
                "accession_id": spec["samples"][g["sample"]]["accession_id"],
                "data_access_link": spec["data_access_link"],
                "download_url": spec["download_url"],
                "dataset_description": (
                    f"HuBMAP MIBI dataset {g['sample']}: one field of view of "
                    f"{spec['samples'][g['sample']].get('tissue', spec['tissue'])} imaged on a "
                    f"{g.get('acquisition_instrument') or 'MIBIscope'} at {g['pixel_size_um']:.3f} "
                    f"um/px, {g['n_cells']} segmented cells, {g['n_antibodies']} antibody targets "
                    f"in a {g['n_channels']}-channel stack. Per-cell values are summed ion counts "
                    "over the submitted segmentation mask."
                ),
            }
            for g in geometry
        ]
    ).to_csv(os.path.join(staging, "dataset_registry.csv"), index=False)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--staging")
    parser.add_argument("--package")
    args = parser.parse_args(argv)

    spec = json.load(open(args.spec))
    key = spec["dataset_key"]
    staging = args.staging or os.path.join(DATA_HOME, "datasets", key, "staging")
    package = args.package or os.path.join(DATA_HOME, "polycomb_data_packages", key)

    with open(os.path.join(staging, "sample_geometry.json")) as handle:
        geometry = json.load(handle)

    collection = Collection(root_dir=package)
    uid_by_sample: dict[str, str] = {}
    for g in geometry:
        dataset = Dataset(g["sample"])
        for path, tag, space in dataset_files(g, staging):
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            dataset.add_file(path, tag, space)
        collection.add_dataset(dataset)
        uid_by_sample[dataset.dataset_name] = dataset.uid

    write_registries(spec, uid_by_sample, geometry, staging)
    write_dataset_registry(spec, geometry, staging)
    for name in (
        "donor_registry.csv",
        "tissuesection_registry.csv",
        "panel_registry.csv",
        "sectionimage_registry.csv",
    ):
        collection.add_file(os.path.join(staging, name), FileTypeTag.LIBRARY)
    collection.add_file(os.path.join(staging, "dataset_registry.csv"), FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(package, MANIFEST)}")
    for g in geometry:
        print(f"  {g['sample']}: {g['n_cells']} cells, section {g['section_id']}")


if __name__ == "__main__":
    main()
