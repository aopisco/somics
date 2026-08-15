"""Drive the viewer from the command line, the way an agent would.

    uv run python -m somics.viewer.drive --node colon --lod section --note "having a good sniff"
    uv run python -m somics.viewer.drive --state
    uv run python -m somics.viewer.drive --tour

Every flag maps to one field of the frontend's ViewerState. The server drops anything it
does not recognise and reports what it dropped, so a typo is visible rather than silent.
"""

import argparse
import json
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8787"

# Section-level cell budget used by the tour; the full section is 587,115 cells.
_TOUR_BUDGET = 120_000

_TOUR = (
    (
        2.0,
        {"species": "rat", "lod": "orbit", "node": None, "sample": None},
        "one whole rat, as promised",
    ),
    (
        3.0,
        {"node": "colon", "lod": "organ"},
        "this is the only organ with any data, so it gets all the attention",
    ),
    (
        3.5,
        {"lod": "section", "budget": _TOUR_BUDGET},
        "diving in — every speck from here on is one real cell",
    ),
    (
        4.0,
        {"paint": "gene", "gene": "EPCAM"},
        "repainting 120,000 cells by how much EPCAM each one is carrying",
    ),
    (3.0, {"lod": "cell"}, "all the way down, where the actual microscope pixels live"),
    (2.0, {"lod": "orbit", "node": None, "sample": None, "paint": "counts"}, "and back up for air"),
)


def post(base: str, patch: dict, note: str | None, actor: str) -> dict:
    request = urllib.request.Request(
        f"{base}/api/control",
        data=json.dumps({"patch": patch, "note": note, "actor": actor}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def get_state(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/control", timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE, help="viewer API root")
    parser.add_argument("--actor", default="agent", help="name shown in the viewer's banner")
    parser.add_argument("--note", help="what you are doing, shown next to the actor")
    parser.add_argument("--state", action="store_true", help="print what the browser is showing")
    parser.add_argument("--tour", action="store_true", help="run a scripted six-step tour")
    parser.add_argument("--species", choices=("human", "rat"))
    parser.add_argument("--node", help="organ node id, e.g. colon; pass '' to clear")
    parser.add_argument("--sample", help="section uid; pass '' to clear")
    parser.add_argument("--lod", choices=("orbit", "organ", "section", "cell"))
    parser.add_argument("--gene", help="gene to paint; pass '' to clear")
    parser.add_argument("--paint", choices=("counts", "gene"))
    parser.add_argument("--budget", type=int, help="cells to request")
    parser.add_argument("--pixel", type=float, help="render scale, 0.15 to 1")
    parser.add_argument("--sound", action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    try:
        if args.state:
            print(json.dumps(get_state(args.base), indent=2))
            return

        if args.tour:
            for pause, patch, note in _TOUR:
                result = post(args.base, patch, note, args.actor)
                print(f"revision {result['revision']}: {note}")
                time.sleep(pause)
            return

        patch: dict = {}
        for key, value in (
            ("species", args.species),
            ("lod", args.lod),
            ("paint", args.paint),
            ("budget", args.budget),
            ("pixel", args.pixel),
            ("sound", args.sound),
        ):
            if value is not None:
                patch[key] = value
        # Empty string means "clear this field", which the viewer models as null.
        for key, value in (("node", args.node), ("sample", args.sample), ("gene", args.gene)):
            if value is not None:
                patch[key] = value or None

        if not patch:
            parser.error("nothing to do — pass at least one field, or --state / --tour")

        result = post(args.base, patch, args.note, args.actor)
        print(json.dumps(result, indent=2))
        if result["dropped"]:
            print(f"dropped (not viewer state): {', '.join(result['dropped'])}")
        if not result["listeners"]:
            print("no browser is listening — open the viewer to watch this land")
    except urllib.error.URLError as error:
        raise SystemExit(f"cannot reach {args.base}: {error}") from None


if __name__ == "__main__":
    main()
