#!/usr/bin/env python3
"""Resolve a table's ontology-aligned columns to CURIEs, in place.

Reconstruction of the hackathon skill script of the same name; see
``scripts/pipeline/_common.py``.

``--from-schema`` reads which columns to resolve off the schema's own
``OntologyAlignedField`` markers rather than being told, which is why the
committed harmonizers can say "the ontology columns are resolved by
``apply_resolution_pass.py --from-schema``" and only handle what it cannot.

What it cannot handle, and skips by design:

- **Ontologies with no entity in polycomb's resolver set.** HANCESTRO is the
  one that bites: its binding is a custom resolver, so ethnicity is resolved by
  the harmonizers instead. Skipping is reported, never silent.
- **Values that do not resolve.** A miss leaves the original text in place and
  is listed. Writing a wrong CURIE is far worse than leaving free text for a
  human, because a CURIE reads as authoritative.

Resolution is exact name/synonym matching against a live OLS, so the same input
can resolve differently as ontologies are revised. That is worth knowing when a
rebuild diffs against an older atlas.

Run:
    python scripts/pipeline/apply_resolution_pass.py <lance_db> \
        --table <ClassName> --schema <schema.yaml> --from-schema [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

import lancedb
import pyarrow as pa
from polycomb.ontologies import OntologyEntity, resolve_ontology_terms
from polycomb.util import load_schema_info

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Which resolver entity each ontology in this schema belongs to. HANCESTRO and
# MmusDv have no entry: the first needs a custom resolver, the second has no
# member in OntologyEntity at all.
ENTITY_BY_ONTOLOGY = {
    "EFO": OntologyEntity.ASSAY,
    "NCBITaxon": OntologyEntity.ORGANISM,
    "UBERON": OntologyEntity.TISSUE,
    "MONDO": OntologyEntity.DISEASE,
    "CL": OntologyEntity.CELL_TYPE,
    "HsapDv": OntologyEntity.DEVELOPMENT_STAGE,
    "PATO": OntologyEntity.SEX,
}


def ontology_columns(cls) -> dict[str, str]:
    out = {}
    for name, field in cls.model_fields.items():
        marker = (getattr(field, "json_schema_extra", None) or {}).get("ontology_aligned")
        if marker:
            out[name] = marker["ontology_name"]
    return out


def resolve_column(values: list, entity, organism: str | None) -> tuple[list, int, list]:
    """Return (new values, n resolved, unresolved originals)."""
    present = sorted({v for v in values if v is not None and str(v).strip() != ""})
    if not present:
        return values, 0, []
    report = resolve_ontology_terms(present, entity, organism=organism)
    mapping = {}
    unresolved = []
    for item in report.results:
        curie = item.ontology_term_id
        if curie:
            mapping[item.input_value] = curie
        else:
            unresolved.append(item.input_value)
    new = [mapping.get(v, v) for v in values]
    return new, len(mapping), unresolved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lance_db")
    ap.add_argument("--table", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--from-schema", action="store_true", required=True)
    ap.add_argument("--organism", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    info = load_schema_info(args.schema)
    # An obs table may be feature-space suffixed; its schema class is the stem.
    class_name = args.table
    if class_name not in info.kinds:
        class_name = next((k for k in info.kinds if args.table.startswith(f"{k}_")), None)
    if class_name is None:
        raise SystemExit(f"{args.table}: no schema class for this table name")

    db = lancedb.connect(args.lance_db)
    if args.table not in db.list_tables().tables:
        print(f"  {args.table}: not present in {args.lance_db}, skipped")
        return 0
    table = db.open_table(args.table).to_arrow()

    wanted = ontology_columns(getattr(info.module, class_name))
    changed = False
    for column, ontology in sorted(wanted.items()):
        if column not in table.column_names:
            continue
        entity = ENTITY_BY_ONTOLOGY.get(ontology)
        if entity is None:
            print(f"  {args.table}.{column}: {ontology} has no resolver entity, skipped")
            continue
        values = table.column(column).to_pylist()
        new, n, unresolved = resolve_column(values, entity, args.organism)
        if unresolved:
            print(f"  {args.table}.{column}: unresolved {unresolved[:5]}")
        if n and new != values:
            index = table.column_names.index(column)
            table = table.set_column(index, column, pa.array(new, pa.string()))
            changed = True
        print(f"  {args.table}.{column} [{ontology}]: {n} value(s) resolved")

    if changed and not args.dry_run:
        db.create_table(args.table, data=table, mode="overwrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
