#!/usr/bin/env python3
"""Write the registries and collection manifest for a Visium package.

The spec-driven counterpart to ``build_visium_package.py``. Everything that
varies by study — donors, sections, blocks, the tissue and its disease state —
comes from the spec; everything structural is derived from what the builder
produced.

Three registries, not four. Visium is whole-transcriptome, so there is no panel
to register and ``panel_uid`` stays null on every obs row. The three that are
written are donors, tissue sections and section images.

Two spec shapes are accepted: a study-level one (LIBD: one tissue, one disease
state, donors with ages) and the per-sample one the 10x catalogue specs use
(``samples[s]["files"]``, ``disease_state``/``disease`` on the sample, donors
with nothing 10x does not publish). Sample-level values win where both exist.

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


def dataset_files(g: dict, staging: str) -> list[tuple[str, FileTypeTag, str | None]]:
    sample = g["sample"]
    d = os.path.join(staging, sample)
    return [
        (os.path.join(d, "filtered_feature_bc_matrix.h5"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, f"{sample}_var.csv"), FileTypeTag.VAR, "gene_expression"),
        (
            os.path.join(d, g.get("image_file", f"{sample}_he_image.tif")),
            FileTypeTag.DATA,
            "discrete_image",
        ),
    ]


def sample_value(spec: dict, entry: dict, key: str):
    """A per-sample field, falling back to the study-level one."""
    return entry[key] if key in entry else spec.get(key)


def image_source(spec: dict, g: dict) -> str:
    if "image_source" in g:
        return g["image_source"]
    return spec["image_url"].format(sample=g["sample"])


def counts_source(spec: dict, sample: str) -> str:
    files = spec["samples"][sample].get("files") or {}
    if "binned_outputs" in files:
        return files["binned_outputs"]
    if "counts" in files:
        return files["counts"]
    return spec["image_url"].format(sample=sample)


IMAGE_DESCRIPTION = {
    "he": (
        "Full-resolution brightfield H&E of the capture area, in the same "
        "pixel frame as the spot coordinates."
    ),
    "immunofluorescence": (
        "Full-resolution immunofluorescence image of the capture area, in the same "
        "pixel frame as the spot coordinates."
    ),
}


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
                "organism": donor.get("organism", spec["organism"]),
                "sex": donor["sex"],
                # Null where the source publishes no age (every 10x catalogue
                # dataset). age_unit is written through the CSV as a plain
                # string so an all-null column stays writable -- see the Xenium
                # assembler for the Lance dictionary-encoding reason.
                "age_value": donor.get("age_value"),
                "age_unit": donor.get(
                    "age_unit", "year" if donor.get("age_value") is not None else None
                ),
                "life_stage": donor["life_stage"],
                "human_development_stage": donor.get("human_development_stage"),
                "description": donor.get("description"),
                # Present even when empty. The resolution pass plans a
                # clinical_diagnosis pass from the schema and errors if the
                # column is absent; a control donor's right value is null.
                "clinical_diagnosis": donor.get("clinical_diagnosis"),
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
                "tissue": sample_value(spec, entry, "tissue"),
                "disease_state": sample_value(spec, entry, "disease_state"),
                # Present even when null. The resolution pass plans one pass per
                # ontology-aligned column it has a resolver for -- tissue and
                # disease here -- and errors if the column is absent rather than
                # skipping it. A healthy section has no disease, so the column
                # must exist and be null.
                "disease": entry.get("disease"),
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
                "source_path": image_source(spec, g),
                "description": IMAGE_DESCRIPTION.get(
                    spec["image_modality"], IMAGE_DESCRIPTION["he"]
                )
                + (
                    f" Padded with background from {g['padded_from_hw'][1]}x"
                    f"{g['padded_from_hw'][0]} px to cover capture-area spots past the edge of "
                    "the microscope scan; crops there are blank."
                    if g.get("padded_from_hw")
                    else ""
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
                "download_url": counts_source(spec, g["sample"]),
                "dataset_description": (
                    f"10x {'Visium HD' if spec['technology'] == 'visium_hd' else 'Visium'} "
                    f"capture area {g['sample']} from the {spec['study_name']}: one "
                    f"{spec['preservation'].replace('_', ' ')} section of "
                    f"{sample_value(spec, spec['samples'][g['sample']], 'tissue')}, "
                    f"{g['n_spots']} {spec['spatial_unit']}s under tissue at "
                    f"{spec['unit_size_um']:g} um, {g['n_features']} features."
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
        for path, tag, space in dataset_files(g, staging):
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
