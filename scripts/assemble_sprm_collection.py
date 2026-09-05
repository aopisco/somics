#!/usr/bin/env python3
"""Write the registries and collection manifest for a Cytokit + SPRM package.

The spec-driven counterpart to ``build_sprm_package.py``. Everything that
varies by dataset -- the donor as HuBMAP describes them, the regions, the
tissue -- comes from the spec; everything structural (image geometry, channel
names, the antigen count) is read from what the builder produced.

Four registries: donors, tissue sections, the antibody panel, and section
images. Unlike Visium there *is* a panel -- the antigens Cytokit extracted are
a targeted set and part of the dataset's identity -- and unlike the Monkman
CODEX package there *is* a donor, because HuBMAP publishes one per dataset.
The donor row is package-local (``<HBM-ID>_donor``), as the Xenium and Visium
packages do, with HuBMAP's own donor id in the description; letting several
packages mint the same donor row is a registry-merge question this adapter does
not decide.

Run:
    python scripts/assemble_sprm_collection.py --spec specs/sprm/<dataset>.json
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
        (os.path.join(d, f"{sample}_protein_intensity.csv"), FileTypeTag.DATA, "protein_abundance"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "protein_abundance"),
        (os.path.join(d, f"{sample}_var.csv"), FileTypeTag.VAR, "protein_abundance"),
        (os.path.join(d, g["image_file"]), FileTypeTag.DATA, "discrete_image"),
    ]


def panel_name(spec: dict, geometry: list[dict]) -> str:
    n = geometry[0]["n_targets"]
    return f"HuBMAP {spec['hubmap_id']} {spec['technology']} panel ({n} targets)"


def write_registries(spec: dict, uid_by_sample: dict[str, str], geometry: list[dict], staging: str):
    pd.DataFrame(
        [
            {
                "donor_id": donor_id,
                "DonorSchema_join": donor_id,
                "organism": donor.get("organism", spec["organism"]),
                "sex": donor["sex"],
                # Written through the CSV so an all-null age_unit stays a plain
                # string column Lance can write (see the Xenium assembler).
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
                "block_id": entry.get("block_id"),
                "section_index": entry.get("section_index", 0),
                "tissue": entry.get("tissue", spec["tissue"]),
                "disease_state": entry.get("disease_state", spec.get("disease_state", "unknown")),
                # Present even when null: the resolution pass plans a disease
                # pass from the schema and errors if the column is absent.
                "disease": entry.get("disease"),
                "preservation": spec.get("preservation", "unknown"),
            }
            for entry in spec["samples"].values()
        ]
    ).to_csv(os.path.join(staging, "tissuesection_registry.csv"), index=False)

    g0 = geometry[0]
    targets = [
        c for c in g0["channel_names"] if c not in _controls(g0["channel_names"], g0["n_targets"])
    ]
    pd.DataFrame(
        [
            {
                "panel_name": panel_name(spec, geometry),
                "PanelSchema_join": panel_name(spec, geometry),
                "vendor": "Akoya Biosciences",
                "version": "study-specific",
                "technology": spec["technology"],
                "organism": spec["organism"],
                "n_targets": g0["n_targets"],
                "has_custom_addon": False,
                "description": (
                    f"The {g0['n_targets']} antibody targets extracted for HuBMAP dataset "
                    f"{spec['hubmap_id']}: {', '.join(targets)}. The feature axis is "
                    f"{g0['n_channels']} columns wide because the nuclear counterstain and any "
                    f"blank or unused channel slots are reported alongside the targets and "
                    f"flagged is_control."
                ),
            }
        ]
    ).to_csv(os.path.join(staging, "panel_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": spec["samples"][g["sample"]]["section_id"],
                "dataset_uid": uid_by_sample[g["sample"]],
                "section_uid_TissueSectionSchema_join": spec["samples"][g["sample"]]["section_id"],
                "image_modality": spec["image_modality"],
                # channel_names is a list column and is added in harmonization,
                # where it can be written as a list rather than a CSV string.
                "pixel_size_um": g["pixel_size_um"],
                "height_px": g["height_px"],
                "width_px": g["width_px"],
                "is_registered_to_expression": True,
                "source_path": g["image_source"],
                "description": (
                    f"The pipeline's stitched {g['n_channels']}-channel {g['image_dtype']} "
                    f"expression "
                    f"extract for region {g['sample']}, rewritten channels-last; every plane is an "
                    f"antibody or counterstain channel named in channel_names. SPRM's cell "
                    f"centroids index this image directly."
                ),
            }
            for g in geometry
        ]
    ).to_csv(os.path.join(staging, "sectionimage_registry.csv"), index=False)


def _controls(channels: list[str], n_targets: int) -> set[str]:
    controls = {
        c for c in channels if c.startswith(("DAPI", "Blank", "Empty", "HOECHST", "Hoechst"))
    }
    if len(channels) - len(controls) != n_targets:
        raise ValueError("control channel count disagrees with the builder's n_targets")
    return controls


def totals_key(spec: dict, sample: str) -> str:
    return spec["samples"][sample]["files"]["cell_channel_total"]


def write_dataset_registry(spec: dict, geometry: list[dict], staging: str) -> None:
    prefix = f"https://assets.hubmapconsortium.org/{spec['uuid']}"
    pd.DataFrame(
        [
            {
                "folder_name": g["sample"],
                "study_name": spec["study_name"],
                "sample_name": spec["samples"][g["sample"]].get(
                    "sample_name", f"{spec['hubmap_id']} region {g['sample']}"
                ),
                "source_dataset_id": spec["dataset_key"],
                "accession_database": spec["accession_database"],
                "accession_id": spec["accession_id"],
                "data_access_link": spec["data_access_link"],
                "download_url": f"{prefix}/{totals_key(spec, g['sample'])}",
                "panel_name": panel_name(spec, geometry),
                "dataset_description": (
                    f"HuBMAP {spec['hubmap_id']}, region {g['sample']}: "
                    f"{spec['technology']} imaging of {spec['tissue']} processed by "
                    f"{spec.get('pipeline', 'Cytokit + SPRM')}. "
                    f"{g['n_cells']} segmented cells x {g['n_channels']} channels "
                    f"({g['n_targets']} antibody targets) at {g['pixel_size_um']:.4f} um/px; the "
                    f"protein readout is SPRM's per-cell summed channel intensity, rounded to "
                    f"integers, and the section image is the stitched expression stack."
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
    missing = [g["sample"] for g in geometry if not g.get("image_built")]
    if missing:
        raise SystemExit(f"image not built for {missing}; run the builder without --skip-image")

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
    collection.add_file(os.path.join(staging, "sample_geometry.json"), FileTypeTag.OTHER)

    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(package, MANIFEST)}")
    for g in geometry:
        print(f"  {g['sample']}: {g['n_cells']} cells, section {g['section_id']}")


if __name__ == "__main__":
    main()
