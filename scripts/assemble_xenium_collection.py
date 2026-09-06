#!/usr/bin/env python3
"""Write the registries and collection manifest for a Xenium package, spec-driven.

The counterpart to ``build_xenium_package.py``, and the same split: everything
that varies by study lives in a spec, everything structural is derived from what
the builder produced.

Four registries here, unlike the Visium assembler's three, because Xenium is a
targeted assay and the panel is part of the dataset's identity — two sections
run on different panels are not directly comparable in feature space.

Both sides of the join convention are written. A ``RegistryKeyField`` resolves
by matching the referrer's ``<field>_<Target>_join`` against the target's own
``<Target>_join``, so the natural key has to appear on both; finalization raises
rather than guessing when it does not.

Run:
    python scripts/assemble_xenium_collection.py --spec specs/<dataset>.json
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd
from polycomb.collection import Collection, Dataset, FileTypeTag

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def dataset_files(sample: str, staging: str) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(staging, sample)
    # HuBMAP submissions carry no metrics_summary.csv; it is provenance, not data.
    return [f for f in _dataset_files(d) if f[1] != FileTypeTag.OTHER or os.path.exists(f[0])]


def _dataset_files(d: str) -> list[tuple[str, FileTypeTag, str | None]]:
    return [
        (os.path.join(d, "cell_feature_matrix.h5"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{sample}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, "cell_feature_matrix_var.csv"), FileTypeTag.VAR, "gene_expression"),
        (os.path.join(d, "morphology_focus.ome.tif"), FileTypeTag.DATA, "discrete_image"),
        (os.path.join(d, "metrics_summary.csv"), FileTypeTag.OTHER, None),
        (os.path.join(d, "experiment.xenium"), FileTypeTag.OTHER, None),
    ]


def write_registries(
    spec: dict, uid_by_sample: dict[str, str], geometry: list[dict], staging: str
) -> None:
    pd.DataFrame(
        [
            {
                "donor_id": donor_id,
                "DonorSchema_join": donor_id,
                "organism": donor["organism"],
                "sex": donor["sex"],
                "life_stage": donor["life_stage"],
                "human_development_stage": donor["human_development_stage"],
                # Declared even when unknown. age_unit is an enum, so the schema
                # types it as an Arrow dictionary -- and Lance cannot write an
                # all-null dictionary column at any row count (empty dictionary,
                # "Value at position 0 out of bounds"). Writing it through the
                # CSV makes it a plain nullable string instead, which Lance
                # accepts and which ensure_schema_columns then leaves alone.
                "age_value": donor.get("age_value"),
                "age_unit": donor.get("age_unit"),
                "clinical_diagnosis": donor.get("clinical_diagnosis"),
                "ethnicity": donor.get("ethnicity"),
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
                "tissue": spec["tissue"],
                "disease_state": entry["disease_state"],
                "disease": entry.get("disease"),
                "preservation": spec["preservation"],
            }
            for entry in spec["samples"].values()
        ]
    ).to_csv(os.path.join(staging, "tissuesection_registry.csv"), index=False)

    panel = dict(spec["panel"])
    # The spec may leave the name and size to the bundle: gene_panel.json names
    # the panel and experiment.xenium counts its targets.
    g0 = geometry[0]
    panel["panel_name"] = panel.get("panel_name") or g0.get("panel_name")
    if not panel["panel_name"]:
        raise ValueError("no panel name in the spec or in the bundle's gene_panel.json")
    if panel.get("n_targets") is None:
        panel["n_targets"] = int(g0["panel_num_targets_predesigned"]) + int(
            g0["panel_num_targets_custom"]
        )
    if panel.get("description") is None and g0.get("panel_name_from_bundle"):
        panel["description"] = f"gene_panel.json names the panel '{g0['panel_name_from_bundle']}'."
    panel["PanelSchema_join"] = panel["panel_name"]
    pd.DataFrame([panel]).to_csv(os.path.join(staging, "panel_registry.csv"), index=False)

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
                "source_path": spec["download_url"].format(sample=g["sample"]),
                "description": (
                    "Single-channel autofocus projection of the nuclear stain, in the same "
                    "pixel frame as the cell centroids."
                    if not g.get("channel_names")
                    else (
                        f"Autofocus projections of the {len(g['channel_names'])} morphology "
                        f"channels ({', '.join(g['channel_names'])}), stacked channels-last, "
                        "in the same pixel frame as the cell centroids."
                    )
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
                "accession_database": spec["accession_database"],
                "data_access_link": spec["data_access_link"],
                "download_url": spec["download_url"].format(sample=g["sample"]),
                "panel_name": spec["panel"].get("panel_name") or g.get("panel_name"),
                "dataset_description": (
                    f"{spec['tissue']} section, {spec['preservation'].upper()}. "
                    f"{g['n_cells']} cells, {g['n_genes_panel']} panel genes of "
                    f"{g['n_features']} feature-axis entries. Xenium Onboard Analysis "
                    f"{g['analysis_sw_version']}, run {g['run_name']} started "
                    f"{g['run_start_time']}, {g['pixel_size_um']} um per pixel, median "
                    f"{g['median_transcripts_per_cell']} transcripts per cell."
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
        dataset = Dataset(g["sample"])
        for path, tag, space in dataset_files(g["sample"], staging):
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
    print(f"wrote {os.path.join(package, 'collection.json')}")
    for g in geometry:
        print(
            f"  {g['sample']}: {g['n_cells']} cells, "
            f"section {spec['samples'][g['sample']]['section_id']}"
        )


if __name__ == "__main__":
    main()
