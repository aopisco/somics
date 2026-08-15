"""Precompute the corpus-builder UI's index from the spatial atlas on R2.

The UI is a browser of the atlas, and the atlas is small enough (13 ingested
datasets, 634k obs rows) that everything worth displaying can be computed once
and written to a JSON file. That keeps the UI backend to "serve a static file"
and means the browser never talks to LanceDB or R2.

One card is one ingested dataset, which for the LIBD DLPFC pilot means one card
per section. `--group-by study` collapses a study's sections into a single card
instead; either way each card carries its members under `sections`.

QC levels use the thresholds from the design packet. They are computed here
rather than stored on the schema — for the hackathon that is the cheap path;
if the UI survives, the same numbers want to move onto SpatialDatasetSchema at
ingest so the verdict is provenance rather than a UI artifact.

Run:
    uv run python scripts/build_corpus_index.py [-o data/corpus_index.json]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Any

import homeobox as hox
import polars as pl

from somics.viewer.atlas_source import DEFAULT_ATLAS_DIR, DEFAULT_STORE_KWARGS

# Platform -> the resolution tier the UI filters on. Not a schema concept: it is
# a property of the instrument, so it lives here as a lookup rather than being
# inferred per row. `spatial_unit` decides the noun (cells/spots/bins), this
# decides the tier.
RESOLUTION_TIER = {
    "xenium": "Subcellular",
    "merfish": "Subcellular",
    "cosmx": "Subcellular",
    "starmap": "Subcellular",
    "starmap_plus": "Subcellular",
    "seqfish": "Subcellular",
    "cartana": "Single-cell",
    "slideseqv2": "Single-cell",
    "visium": "Spot",
    "visium_hd": "Spot",
}

PLATFORM_LABEL = {
    "visium": "Visium",
    "visium_hd": "Visium HD",
    "xenium": "Xenium",
    "merfish": "MERFISH",
    "cosmx": "CosMx",
    "cartana": "CARTANA",
    "slideseqv2": "Slide-seqV2",
    "starmap": "STARmap",
    "starmap_plus": "STARmap PLUS",
    "seqfish": "seqFISH",
}

UNIT_NOUN = {"cell": "cells", "nucleus": "nuclei", "spot": "spots", "bin": "bins", "bead": "beads"}

# Capture-based platforms have no negative-control probes and no segmentation,
# and their per-unit counts are not comparable to per-cell transcript counts —
# so those metrics are "na" rather than scored. See docs/ for the reasoning.
CAPTURE_PLATFORMS = {"visium", "visium_hd", "slideseqv2"}


def _level(value: float | None, pass_at: float, warn_at: float, higher_is_better: bool) -> str:
    """Score a value against a pass/warn pair; below warn is a fail."""
    if value is None:
        return "na"
    if higher_is_better:
        if value >= pass_at:
            return "pass"
        return "warn" if value >= warn_at else "fail"
    if value <= pass_at:
        return "pass"
    return "warn" if value <= warn_at else "fail"


def _metric(
    key: str, short: str, label: str, value: str, numeric: float | None, threshold: str, level: str
) -> dict[str, Any]:
    return {
        "key": key,
        "short": short,
        "label": label,
        "value": value,
        "numeric": numeric,
        "threshold": threshold,
        "level": level,
    }


def build_qc(technology: str, agg: dict[str, Any]) -> list[dict[str, Any]]:
    """The six packet metrics for one card, with unavailable ones marked na."""
    capture = technology in CAPTURE_PLATFORMS
    median_counts = agg["median_counts"]

    if capture:
        # Counts per 55 µm spot are not transcripts per cell; showing them
        # against a per-cell threshold would score every Visium dataset well
        # for the wrong reason.
        counts_metric = _metric(
            "tx_per_cell",
            "tx/cell",
            "Median transcripts/cell",
            f"{median_counts:,.0f} / spot" if median_counts is not None else "—",
            median_counts,
            "not applicable to capture assays",
            "na",
        )
    else:
        counts_metric = _metric(
            "tx_per_cell",
            "tx/cell",
            "Median transcripts/cell",
            f"{median_counts:,.0f}" if median_counts is not None else "—",
            median_counts,
            "target ≥ 200",
            _level(median_counts, 200, 100, higher_is_better=True),
        )

    neg_rate = agg["neg_rate_pct"]
    neg_metric = _metric(
        "neg_probe",
        "neg-probe",
        "Negative-probe rate",
        f"{neg_rate:.3f}%" if neg_rate is not None else "—",
        neg_rate,
        "no negative probes on this assay" if capture else "target ≤ 0.5%",
        "na" if capture or neg_rate is None else _level(neg_rate, 0.5, 1.0, higher_is_better=False),
    )

    # Neither of these has a source in the atlas: the assigned fraction lives in
    # the platform's own run metrics (not ingested), and no segmentation verdict
    # is stored anywhere — only `segmentation_method`.
    assigned_metric = _metric(
        "assigned",
        "assigned",
        "% transcripts assigned to cells",
        "—",
        None,
        "not captured at ingest",
        "na",
    )
    seg_metric = _metric(
        "segmentation",
        "seg",
        "Segmentation quality",
        agg["segmentation_method"] or "—",
        None,
        "no segmentation on this assay" if capture else "no verdict stored at ingest",
        "na",
    )
    return [counts_metric, neg_metric, assigned_metric, seg_metric]


def aggregate_obs(atlas: hox.RaggedAtlas) -> pl.DataFrame:
    """One row per ingested dataset, carrying everything obs can tell us."""
    obs = (
        atlas.query()
        .select(
            [
                "dataset_uid",
                "section_uid",
                "donor_uid",
                "panel_uid",
                "technology",
                "spatial_unit",
                "segmentation_method",
                "tissue",
                "disease",
                "disease_state",
                "organism",
                "assay",
                "n_counts",
                "negative_control_counts",
                "unassigned_counts",
                "has_gene_expression",
                "has_protein_abundance",
                "has_he_crop",
                "has_morphology_crop",
            ]
        )
        .to_polars()
    )
    # Enum columns arrive as Arrow dictionaries; plain strings are easier here.
    enums = ["technology", "spatial_unit", "segmentation_method", "disease_state"]
    obs = obs.with_columns([pl.col(c).cast(pl.Utf8) for c in enums])

    return obs.group_by("dataset_uid").agg(
        pl.len().alias("n_units"),
        pl.col("section_uid").n_unique().alias("n_sections"),
        pl.col("section_uid").drop_nulls().unique().alias("section_uids"),
        pl.col("donor_uid").drop_nulls().unique().alias("donor_uids"),
        pl.col("panel_uid").drop_nulls().unique().alias("panel_uids"),
        pl.col("technology").first(),
        pl.col("spatial_unit").first(),
        pl.col("segmentation_method").first(),
        pl.col("tissue").drop_nulls().unique().alias("tissues"),
        pl.col("disease").drop_nulls().unique().alias("diseases"),
        pl.col("disease_state").first(),
        pl.col("organism").drop_nulls().unique().alias("organisms"),
        pl.col("assay").drop_nulls().unique().alias("assays"),
        pl.col("n_counts").median().alias("median_counts"),
        pl.col("n_counts").sum().alias("total_counts"),
        pl.col("negative_control_counts").sum().alias("total_neg"),
        pl.col("unassigned_counts").sum().alias("total_unassigned"),
        pl.col("has_gene_expression").any().alias("has_gene_expression"),
        pl.col("has_protein_abundance").any().alias("has_protein_abundance"),
        pl.col("has_he_crop").any().alias("has_he_crop"),
        pl.col("has_morphology_crop").any().alias("has_morphology_crop"),
    )


def _lookup(atlas: hox.RaggedAtlas, table: str) -> pl.DataFrame | None:
    if table not in atlas.db.list_tables().tables:
        return None
    return atlas.db.open_table(table).search().to_polars()


def _nan_to_none(value: Any) -> float | None:
    """Unpopulated numerics come back as NaN, not null."""
    if value is None:
        return None
    value = float(value)
    return None if value != value else value


def build_index(
    atlas: hox.RaggedAtlas, atlas_dir: str, group_by: str = "dataset"
) -> dict[str, Any]:
    # list_datasets() has one row per dataset *per feature space*, so summing
    # n_rows over it double-counts. Dedupe on dataset_uid first.
    datasets = atlas.list_datasets()
    feature_spaces = {
        uid: sorted(group["feature_space"].drop_nulls().unique().to_list())
        for uid, group in datasets.group_by("dataset_uid")
        for uid in [group["dataset_uid"][0]]
    }
    datasets = datasets.unique(subset=["dataset_uid"], keep="first")
    agg = aggregate_obs(atlas)
    datasets = datasets.join(agg, on="dataset_uid", how="left")

    donors = _lookup(atlas, "DonorSchema")
    panels = _lookup(atlas, "PanelSchema")
    publications = _lookup(atlas, "PublicationSchema")

    def donor_ids(uids: list[str]) -> list[str]:
        if donors is None or not uids:
            return []
        rows = donors.filter(pl.col("uid").is_in(uids))
        return sorted(rows["donor_id"].drop_nulls().unique().to_list())

    def panel_row(uids: list[str]) -> dict[str, Any] | None:
        if panels is None or not uids:
            return None
        rows = panels.filter(pl.col("uid").is_in(uids))
        return rows.row(0, named=True) if rows.height else None

    def publication(uid: str | None) -> dict[str, Any] | None:
        if publications is None or uid is None:
            return None
        rows = publications.filter(pl.col("uid") == uid)
        return rows.row(0, named=True) if rows.height else None

    members: list[dict[str, Any]] = []
    for row in datasets.iter_rows(named=True):
        technology = row["technology"] or "other"
        panel = panel_row(row["panel_uids"] or [])
        pub = publication(row["publication_uid"])
        released = pub["publication_date"] if pub and pub.get("publication_date") else None
        members.append(
            {
                "datasetUid": row["dataset_uid"],
                "sampleName": row["sample_name"],
                "studyName": row["study_name"],
                "technology": technology,
                "platform": PLATFORM_LABEL.get(technology, technology),
                "modality": "Proteomics" if row["has_protein_abundance"] else "Transcriptomics",
                "resolution": RESOLUTION_TIER.get(technology, "Single-cell"),
                "unitCount": int(row["n_units"] or 0),
                "unitNoun": UNIT_NOUN.get(row["spatial_unit"] or "", "units"),
                "sectionCount": int(row["n_sections"] or 0),
                "sectionUids": list(row["section_uids"] or []),
                "tissues": row["tissues"] or [],
                "diseases": row["diseases"] or [],
                "diseaseState": row["disease_state"],
                "organisms": row["organisms"] or [],
                "assays": row["assays"] or [],
                "hasImages": bool(row["has_he_crop"] or row["has_morphology_crop"]),
                "imageKinds": [
                    kind
                    for kind, present in (
                        ("H&E", row["has_he_crop"]),
                        ("morphology", row["has_morphology_crop"]),
                    )
                    if present
                ],
                "featureSpaces": feature_spaces.get(row["dataset_uid"], []),
                "downloadUrl": row["download_url"],
                "accessLink": row["data_access_link"],
                "accession": row["accession_id"],
                "accessionDatabase": row["accession_database"],
                "description": row["dataset_description"],
                "donorIds": donor_ids(row["donor_uids"] or []),
                "panelName": panel["panel_name"] if panel else None,
                "panelSize": panel["n_targets"] if panel else None,
                "panelVersion": panel["version"] if panel else None,
                "publicationTitle": pub["title"] if pub else None,
                "publicationDoi": pub["doi"] if pub else None,
                "released": released.date().isoformat() if released else None,
                "medianCounts": _nan_to_none(row["median_counts"]),
                "totalCounts": _nan_to_none(row["total_counts"]),
                "totalNeg": _nan_to_none(row["total_neg"]),
                "totalUnassigned": _nan_to_none(row["total_unassigned"]),
                "segmentationMethod": row["segmentation_method"],
            }
        )

    cards = [build_card(group) for group in group_members(members, group_by)]
    cards.sort(key=lambda card: -card["unitCount"])
    return {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "atlasDir": atlas_dir,
        "stats": build_stats(cards),
        "facets": build_facets(cards),
        "starterPrompts": build_starter_prompts(cards),
        "datasets": cards,
    }


def group_members(members: list[dict[str, Any]], mode: str) -> list[list[dict[str, Any]]]:
    """Split ingested datasets into cards.

    `dataset` gives one card per ingested dataset — for the DLPFC pilot that is
    one card per section, which is what the grid renders. `study` collapses a
    study's sections into a single card instead.
    """
    if mode == "dataset":
        return [[member] for member in members]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        key = member["studyName"] or member["datasetUid"]
        grouped.setdefault(key, []).append(member)
    return list(grouped.values())


def card_title(head: dict[str, Any], members: list[dict[str, Any]]) -> str:
    """A title that reads on its own.

    Section labels are often bare capture-area numbers ("151507"), which say
    nothing without their study; descriptive sample names stand alone.
    """
    if len(members) > 1:
        return head["studyName"] or head["sampleName"] or head["datasetUid"]
    sample = head["sampleName"]
    if not sample:
        return head["studyName"] or head["datasetUid"]
    if head["studyName"] and len(sample) < 12:
        return f"{head['studyName']} {sample}"
    return sample


def _unique(values: list[Any]) -> list[Any]:
    return sorted({v for v in values if v is not None})


def build_card(members: list[dict[str, Any]]) -> dict[str, Any]:
    head = members[0]
    unit_count = sum(m["unitCount"] for m in members)
    total_counts = sum(m["totalCounts"] or 0 for m in members)
    total_neg = sum(m["totalNeg"] or 0 for m in members)
    total_unassigned = sum(m["totalUnassigned"] or 0 for m in members)
    denominator = total_counts + total_neg + total_unassigned

    # A study's sections share a platform and panel, so the median of their
    # medians is a fair enough summary at this corpus size.
    medians = [m["medianCounts"] for m in members if m["medianCounts"] is not None]
    median_counts = sorted(medians)[len(medians) // 2] if medians else None

    qc = build_qc(
        head["technology"],
        {
            "median_counts": median_counts,
            "neg_rate_pct": (100 * total_neg / denominator) if denominator and total_neg else None,
            "segmentation_method": head["segmentationMethod"],
        },
    )
    diseases = _unique([d for m in members for d in m["diseases"]])
    if diseases:
        # `disease` is null for healthy tissue, so `disease_state` is what
        # separates "healthy" from "never annotated".
        disease = ", ".join(diseases)
    elif all(m["diseaseState"] == "healthy" for m in members):
        disease = "Healthy"
    else:
        disease = "—"
    return {
        "id": head["datasetUid"]
        if len(members) == 1
        else (head["studyName"] or head["datasetUid"]),
        "title": card_title(head, members),
        "study": head["studyName"],
        "platform": head["platform"],
        "modality": head["modality"],
        "tissue": ", ".join(_unique([t for m in members for t in m["tissues"]])) or "—",
        "disease": disease,
        "resolution": head["resolution"],
        "unitCount": unit_count,
        "unitNoun": head["unitNoun"],
        "sectionCount": sum(m["sectionCount"] for m in members),
        "datasetCount": len(members),
        "hasImages": any(m["hasImages"] for m in members),
        "imageKinds": _unique([k for m in members for k in m["imageKinds"]]),
        "downloadable": any(m["downloadUrl"] for m in members),
        "qc": qc,
        "passesAllQc": all(m["level"] in ("pass", "na") for m in qc),
        "meta": {
            "organism": ", ".join(_unique([o for m in members for o in m["organisms"]])) or "—",
            "donorIds": _unique([d for m in members for d in m["donorIds"]]),
            "panelName": head["panelName"],
            "panelSize": head["panelSize"],
            "panelVersion": head["panelVersion"],
            "referenceGenome": None,
            "license": None,
            "released": head["released"],
            "publicationTitle": head["publicationTitle"],
            "publicationDoi": head["publicationDoi"],
            "accession": head["accession"],
            "accessionDatabase": head["accessionDatabase"],
            "assays": _unique([a for m in members for a in m["assays"]]),
            "featureSpaces": _unique([f for m in members for f in m["featureSpaces"]]),
            "description": head["description"],
        },
        "location": head["accessLink"] or head["downloadUrl"],
        "downloadUrl": head["downloadUrl"],
        "sections": [
            {
                "datasetUid": m["datasetUid"],
                "sampleName": m["sampleName"],
                "unitCount": m["unitCount"],
                "unitNoun": m["unitNoun"],
                "sectionUids": m["sectionUids"],
                "medianCounts": m["medianCounts"],
                "downloadUrl": m["downloadUrl"],
            }
            for m in sorted(members, key=lambda m: str(m["sampleName"]))
        ],
    }


def build_stats(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "datasets": len(cards),
        "ingestedDatasets": sum(card["datasetCount"] for card in cards),
        "units": sum(card["unitCount"] for card in cards),
        "sections": sum(card["sectionCount"] for card in cards),
        "platforms": len({card["platform"] for card in cards}),
        "passAllQc": sum(1 for card in cards if card["passesAllQc"]),
    }


def build_facets(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Facet vocabularies with total counts.

    The UI recomputes counts per query with the group's own filter dropped —
    at this corpus size that is a loop over the card list, not a query.
    """
    groups = {
        "modality": lambda c: [c["modality"]],
        "platform": lambda c: [c["platform"]],
        "tissue": lambda c: [t.strip() for t in c["tissue"].split(",") if t.strip() != "—"],
        "disease": lambda c: [d.strip() for d in c["disease"].split(",") if d.strip() != "—"],
        "resolution": lambda c: [c["resolution"]],
    }
    facets: dict[str, list[dict[str, Any]]] = {}
    for name, extract in groups.items():
        counts: dict[str, int] = {}
        for card in cards:
            for value in extract(card):
                counts[value] = counts.get(value, 0) + 1
        facets[name] = [{"value": value, "count": count} for value, count in sorted(counts.items())]
    return facets


def build_starter_prompts(cards: list[dict[str, Any]]) -> list[str]:
    """Prompts that mirror the corpus, so a click always returns something."""
    prompts: list[str] = []
    for card in cards:
        tissue = card["tissue"].split(",")[0].strip()
        if card["hasImages"]:
            prompt = f"{tissue}, {card['modality'].lower()} with images"
        else:
            prompt = f"{tissue} {card['platform']}"
        if prompt not in prompts:
            prompts.append(prompt)
    return prompts[:3]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="data/corpus_index.json")
    parser.add_argument(
        "--atlas",
        default=os.environ.get("SOMICS_ATLAS_DIR", DEFAULT_ATLAS_DIR),
        help="Atlas directory; defaults to the public R2 bucket.",
    )
    parser.add_argument(
        "--group-by",
        choices=["dataset", "study"],
        default="dataset",
        help="One card per ingested dataset (default) or per study.",
    )
    args = parser.parse_args()

    store_kwargs = DEFAULT_STORE_KWARGS if args.atlas.startswith("s3://") else None
    print(f"reading {args.atlas}")
    atlas = hox.RaggedAtlas.checkout_latest(args.atlas, store_kwargs=store_kwargs)
    index = build_index(atlas, args.atlas, group_by=args.group_by)

    with open(args.output, "w") as handle:
        json.dump(index, handle, indent=2)
        handle.write("\n")

    stats = index["stats"]
    print(
        f"wrote {args.output}: {stats['datasets']} cards from "
        f"{stats['ingestedDatasets']} ingested datasets, {stats['units']:,} units, "
        f"{stats['passAllQc']}/{stats['datasets']} passing all QC"
    )


if __name__ == "__main__":
    main()
