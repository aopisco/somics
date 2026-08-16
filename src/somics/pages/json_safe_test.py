"""NaN must never reach a page payload.

`json.dumps` writes bare `NaN`, which is not JSON; `JSON.parse` then rejects the
whole payload and the page renders blank rather than degrading. The atlas hands
back NaN for any column an assay does not measure — a CODEX core has no
transcript counts — so this is the guard on that path.
"""

import importlib.util
import json
import math
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "build_dataset_pages",
    Path(__file__).resolve().parents[3] / "scripts" / "build_dataset_pages.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
json_safe = _module.json_safe


def test_bare_nan_is_not_valid_json():
    """The failure this guards against, stated as a test."""
    with pytest.raises(ValueError):
        json.dumps({"nCounts": math.nan}, allow_nan=False)


def test_nan_and_infinities_become_null():
    payload = {
        "nCounts": math.nan,
        "high": math.inf,
        "low": -math.inf,
        "kept": 12.5,
        "tiles": [{"x": math.nan}, {"x": 3.0}],
        "range": (math.nan, 1.0),
    }
    safe = json_safe(payload)

    assert safe["nCounts"] is None
    assert safe["high"] is None and safe["low"] is None
    assert safe["kept"] == 12.5
    assert safe["tiles"] == [{"x": None}, {"x": 3.0}]
    assert safe["range"] == [None, 1.0]

    # The whole point: it now survives a strict dump and round-trips.
    assert json.loads(json.dumps(safe, allow_nan=False)) == safe


def test_non_float_values_pass_through():
    payload = {"name": "PanCK", "n": 7, "flag": True, "missing": None}
    assert json_safe(payload) == payload
