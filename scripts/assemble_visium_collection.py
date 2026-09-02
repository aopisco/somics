#!/usr/bin/env python3
"""Write the registries and collection manifest for a Visium package.

The spec-driven counterpart to ``build_visium_package.py``. Everything that
varies by study — donors, sections, blocks, the tissue and its disease state —
comes from the spec; everything structural is derived from what the builder
produced.

Three registries, not four. Visium is whole-transcriptome, so there is no panel
to register and ``panel_uid`` stays null on every obs row. The three that are
written are donors, tissue sections and section images.

``section_id`` and ``donor_id`` are taken verbatim from the spec rather than
composed from a study prefix. Their uids are content hashes of exactly these
strings, so a rebuild only reproduces a published section if the string matches
character for character — which is a good reason not to let a convention
generate them.

Run:
    python scripts/assemble_visium_collection.py --spec specs/<dataset>.json
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd
from polycomb.collection import Collection, Dataset, FileTypeTag

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")
MANIFEST = "collection.json"


def dataset_files(sample: str, staging: str) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(staging, sample)
    return [
        (os.path.join(d, "filtered_feature_bc_matrix.h5"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, f"{sample}_var.csv"), FileTypeTag.VAR, "gene_expression"),
        (os.path.join(d, f"{sample}_he_image.tif"), FileTypeTag.DATA, "discrete_image"),
    ]


def write_registries(
    spec: dict, uid_by_sample: dict[str, str], geometry: dict, staging: str
) -> None:
    pd.DataFrame(
        [
            {
                "donor_id": donor_id,
                # Target side of the join convention. A RegistryKeyField
                # resolves by matching a referrer's <field>_<Target>_join
                # against the target's own <Target>_join, so the target has to
                # carry the natural key too -- finalization raises rather than
                # guessing if it is absent.
                "DonorSchema_join": donor_id,
                "organism": spec["organism"],
                "sex": donor["sex"],
                "age_value": donor["age_value"],
                "age_unit": "year",
                "life_stage": donor["life_stage"],
                "human_development_stage": donor["human_development_stage"],
                "description": donor.get("description"),
                # Present but empty on purpose. The resolution pass plans a
                # clinical_diagnosis pass from the schema and errors if the
                # column is absent; these donors are neurotypical controls, so
                # the right value is null rather than a diagnosis.
                "clinical_diagnosis": None,
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
                "section_index": entry["section_index"],
                "tissue": spec["tissue"],
                "disease_state": spec["disease_state"],
                # Empty on purpose, like clinical_diagnosis above. The
                # resolution pass plans one pass per ontology-aligned column it
                # has a resolver for -- tissue and disease here -- and errors if
                # the column is absent rather than skipping it. A healthy
                # section has no disease, so the column must exist and be null.
                "disease": None,
                "preservation": spec["preservation"],
            }
            for entry in spec["samples"].values()
        ]
    ).to_csv(os.path.join(staging, "tissuesection_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": spec["samples"][g["sample"]]["section_id"],
                # The pointer an image fills is resolved by joining
                # SectionImageSchema.dataset_uid to the manifest. Without it the
                # map is empty and ingestion falls back to morphology_crop --
                # silently correct for a DAPI stack, silently wrong for H&E.
                "dataset_uid": uid_by_sample[g["sample"]],
                "section_uid_TissueSectionSchema_join": (
                    spec["samples"][g["sample"]]["section_id"]
                ),
                "image_modality": spec["image_modality"],
                "pixel_size_um": g["pixel_size_um"],
                "height_px": g["height_px"],
                "width_px": g["width_px"],
                "is_registered_to_expression": True,
                "source_path": spec["image_url"].format(sample=g["sample"]),
                "description": (
                    "Full-resolution brightfield H&E of the capture area, in the same "
                    "pixel frame as the spot coordinates."
                ),
            }
            for g in geometry
        ]
    ).to_csv(os.path.join(staging, "sectionimage_registry.csv"), index=False)


def write_dataset_registry(spec: dict, geometry: dict, staging: str) -> None:
    pd.DataFrame(
        [
            {
                "folder_name": g["sample"],
                "study_name": spec["study_name"],
                "sample_name": g["sample"],
                "accession_database": spec["accession_database"],
                "accession_id": spec["samples"][g["sample"]]["accession_id"],
                "data_access_link": spec["data_access_link"],
                "download_url": spec["image_url"].format(sample=g["sample"]),
                "dataset_description": (
                    f"10x Visium capture area {g['sample']} from the "
                    f"{spec['study_name']}: one {spec['preservation'].replace('_', ' ')} "
                    f"section of {spec['tissue']}, {g['n_spots']} spots under tissue at "
                    f"{spec['unit_size_um']:.0f} um, {g['n_features']} features."
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
    key = spec.get("dataset_key") or os.path.splitext(os.path.basename(args.spec))[0]
    staging = args.staging or os.path.join(DATA_HOME, "datasets", key, "staging")
    package = args.package or os.path.join(DATA_HOME, "polycomb_data_packages", key)

    with open(os.path.join(staging, "sample_geometry.json")) as handle:
        geometry = json.load(handle)

    collection = Collection(root_dir=package)
    uid_by_sample: dict[str, str] = {}
    for g in geometry:
        sample = g["sample"]
        dataset = Dataset(sample)
        for path, tag, space in dataset_files(sample, staging):
            dataset.add_file(path, tag, space)
        collection.add_dataset(dataset)
        uid_by_sample[dataset.dataset_name] = dataset.uid

    write_registries(spec, uid_by_sample, geometry, staging)
    write_dataset_registry(spec, geometry, staging)

    for name in ("donor_registry.csv", "tissuesection_registry.csv", "sectionimage_registry.csv"):
        collection.add_file(os.path.join(staging, name), FileTypeTag.LIBRARY)
    # Provenance the dataset harmonization reads, not a schema table.
    collection.add_file(os.path.join(staging, "dataset_registry.csv"), FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(package, MANIFEST)}")
    for g in geometry:
        print(f"  {g['sample']}: {g['n_spots']} spots, section {g['section_id']}")


if __name__ == "__main__":
    main()
