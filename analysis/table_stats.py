"""Print headline counts for the three inventory tables.

Usage: uv run python analysis/table_stats.py
"""

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    with open(REPO / "data" / "model_dataset_usage.csv") as f:
        usage = list(csv.DictReader(f))
    print("usage rows:", len(usage))
    print("models:", len({r["model"] for r in usage}))
    print("datasets used:", len({r["dataset_id"] for r in usage}))

    with open(REPO / "data" / "literature_datasets.csv") as f:
        lit = list(csv.DictReader(f))
    print("claim rows:", len(lit), "| source papers:", len({r["source_paper_id"] for r in lit}))

    with open(REPO / "data" / "datasets.csv") as f:
        ds = list(csv.DictReader(f))
    print("curated datasets:", len(ds))


if __name__ == "__main__":
    main()
