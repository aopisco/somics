#!/usr/bin/env python3
"""Registries and collection manifest for a MERFISH package, spec- and geometry-driven.

The Xenium assembler's shape with two differences the source forces:

- Sections and donors come from the builder's ``sample_geometry.json``, not the
  spec. An Allen release's sections are whatever its cell metadata lists, and
  the donor (one animal per release, sex and genotype recorded per cell) is read
  from the same file. The spec carries what the metadata does not: organism,
  life stage, tissue, preservation, the panel, and the release's provenance.
- No ``sectionimage_registry.csv`` unless a section has an image. Allen publishes
  no per-section imagery, so the collection has one feature space
  (``gene_expression``) and the runner skips the image library table.

Run:
    python scripts/assemble_merfish_collection.py --spec specs/merfish/<dataset>.json
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd
from polycomb.collection import Collection, Dataset, FileTypeTag

DATA_HOME = os.environ.get("SOMICS_DATA_HOME", "/home/ubuntu")


def dataset_files(g: dict, staging: str) -> list[tuple[str, FileTypeTag, str | None]]:
    d = os.path.join(staging, g["sample"])
    files = [
        (os.path.join(d, "cell_feature_matrix.h5"), FileTypeTag.DATA, "gene_expression"),
        (os.path.join(d, f"{g['sample']}_obs.csv"), FileTypeTag.OBS, "gene_expression"),
        (os.path.join(d, "cell_feature_matrix_var.csv"), FileTypeTag.VAR, "gene_expression"),
    ]
    if g.get("image_file"):
        files.append((os.path.join(d, g["image_file"]), FileTypeTag.DATA, "discrete_image"))
    return files


def write_registries(spec: dict, uid_by_sample: dict[str, str], geometry: list[dict], staging: str) -> bool:
    donors_spec = spec.get("donors", {})
    donors: dict[str, dict] = {}
    for g in geometry:
        d = g["donor"]
        base = donors_spec.get(d["donor_id"], {})
        donors[d["donor_id"]] = {
            "donor_id": d["donor_id"],
            "DonorSchema_join": d["donor_id"],
            "organism": spec["organism"],
            "sex": base.get("sex") or d.get("sex") or "unknown",
            "life_stage": base.get("life_stage") or spec.get("life_stage", "unknown"),
            "human_development_stage": base.get("human_development_stage"),
            "mouse_development_stage": base.get("mouse_development_stage"),
            "age_value": base.get("age_value"),
            "age_unit": base.get("age_unit"),
            "clinical_diagnosis": base.get("clinical_diagnosis"),
            "ethnicity": base.get("ethnicity"),
            "description": base.get("description")
            or (
                f"Donor {d['donor_id']} of the {spec['study_name']} release"
                + (f"; genotype {d['genotype']} as the cell metadata records it." if d.get("genotype") else ".")
            ),
        }
    pd.DataFrame(list(donors.values())).to_csv(os.path.join(staging, "donor_registry.csv"), index=False)

    pd.DataFrame(
        [
            {
                "section_id": g["section_id"],
                "TissueSectionSchema_join": g["section_id"],
                "donor_id": g["donor_id"],
                "donor_uid_DonorSchema_join": g["donor_id"],
                "block_id": (spec.get("samples") or {}).get(g["sample"], {}).get("block_id", spec.get("block_id")),
                "section_index": i,
                "tissue": spec["tissue"],
                "disease_state": (spec.get("samples") or {}).get(g["sample"], {}).get("disease_state", spec.get("disease_state", "unknown")),
                "disease": (spec.get("samples") or {}).get(g["sample"], {}).get("disease", spec.get("disease")),
                "preservation": spec["preservation"],
            }
            for i, g in enumerate(geometry)
        ]
    ).to_csv(os.path.join(staging, "tissuesection_registry.csv"), index=False)

    panel = dict(spec["panel"])
    if panel.get("n_targets") is None:
        panel["n_targets"] = int(geometry[0]["n_genes_panel"])
    panel["PanelSchema_join"] = panel["panel_name"]
    pd.DataFrame([panel]).to_csv(os.path.join(staging, "panel_registry.csv"), index=False)

    with_image = [g for g in geometry if g.get("image_file")]
    if with_image:
        pd.DataFrame(
            [
                {
                    "section_id": g["section_id"],
                    "dataset_uid": uid_by_sample[g["sample"]],
                    "section_uid_TissueSectionSchema_join": g["section_id"],
                    "image_modality": spec["image_modality"],
                    "pixel_size_um": g["pixel_size_um"],
                    "height_px": g["height_px"],
                    "width_px": g["width_px"],
                    "is_registered_to_expression": True,
                    "source_path": spec["download_url"],
                    "description": g.get("image_description"),
                }
                for g in with_image
            ]
        ).to_csv(os.path.join(staging, "sectionimage_registry.csv"), index=False)
    return bool(with_image)


def write_dataset_registry(spec: dict, geometry: list[dict], staging: str) -> None:
    pd.DataFrame(
        [
            {
                "folder_name": g["sample"],
                "study_name": spec["study_name"],
                "sample_name": (spec.get("samples") or {}).get(g["sample"], {}).get("sample_name", g["section_id"]),
                "accession_database": spec["accession_database"],
                "data_access_link": spec["data_access_link"],
                "download_url": spec["download_url"],
                "panel_name": spec["panel"]["panel_name"],
                "dataset_description": (
                    f"{spec['tissue']} section {g['section_id']}, {spec['preservation'].replace('_', ' ')}. "
                    f"{g['n_cells']} cells, {g['n_genes_panel']} panel genes of {g['n_features']} "
                    f"feature-axis entries, median {g['median_transcripts_per_cell']:.0f} transcripts per cell. "
                    + (f"Section at z = {g['z_mm']} mm in the release's coordinate frame. " if g.get("z_mm") is not None else "")
                    + ("Expression only: the release publishes no per-section image." if not g.get("image_file") else "With the section's DAPI image.")
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
    geometry = json.load(open(os.path.join(staging, "sample_geometry.json")))

    collection = Collection(root_dir=package)
    uid_by_sample: dict[str, str] = {}
    for g in geometry:
        dataset = Dataset(g["sample"])
        while dataset.uid.isdigit():  # an all-digit uid comes back from CSV staging as an int
            dataset = Dataset(g["sample"])
        for path, tag, space in dataset_files(g, staging):
            dataset.add_file(path, tag, space)
        collection.add_dataset(dataset)
        uid_by_sample[dataset.dataset_name] = dataset.uid

    has_images = write_registries(spec, uid_by_sample, geometry, staging)
    write_dataset_registry(spec, geometry, staging)
    libraries = ["donor_registry.csv", "tissuesection_registry.csv", "panel_registry.csv"]
    if has_images:
        libraries.append("sectionimage_registry.csv")
    for name in libraries:
        collection.add_file(os.path.join(staging, name), FileTypeTag.LIBRARY)
    collection.add_file(os.path.join(staging, "dataset_registry.csv"), FileTypeTag.OTHER)
    collection.coalesce(copy=False)
    collection.to_json()
    print(f"wrote {os.path.join(package, 'collection.json')}: {len(geometry)} section(s), "
          f"{sum(g['n_cells'] for g in geometry)} cells, images={has_images}")


if __name__ == "__main__":
    main()
