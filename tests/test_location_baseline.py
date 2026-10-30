from pathlib import Path

import pytest
from lyra.sdk import Locations
from lyra.sdk.cache import TableCache
from pydantic import ValidationError

from benchmarks.location_baseline.fixtures import (
    assert_exact,
    exact,
    invalid_inputs,
    payload,
    variants,
)
from benchmarks.location_baseline.run import measure


def test_fixtures() -> None:
    for raw in variants().values():
        value = Locations.model_validate(raw)
        assert_exact(value, raw)
    for raw in invalid_inputs().values():
        with pytest.raises(ValidationError):
            Locations.model_validate(raw)


def test_fixture_scalar_coverage() -> None:
    properties = [feature["properties"] for feature in payload()["features"]]
    values = {exact(item["value"]) for item in properties if "value" in item}
    assert values == {exact(value) for value in (True, 1, 1.0, 2**60 + 1, "text", None)}
    assert {} in properties


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (True, 1),
        (1, 1.0),
        ({}, {"x": None}),
        ([1, 2], [2, 1]),
        (0.0, -0.0),
        (2**60, 2**60 + 1),
        (
            {"type": "Point", "coordinates": [1.0, 2.0]},
            {"type": "MultiPoint", "coordinates": [[1.0, 2.0]]},
        ),
        ([[1.0, 2.0], [3.0, 4.0]], [[3.0, 4.0], [1.0, 2.0]]),
    ],
)
def test_exact_rejects_loss(a: object, b: object) -> None:
    with pytest.raises(AssertionError):
        assert_exact(a, b)


def test_variants() -> None:
    values = variants()
    assert_exact(values["base"], values["reordered"])
    for name in ("scope", "crs", "null", "changed", "feature_order", "empty"):
        with pytest.raises(AssertionError):
            assert_exact(values["base"], values[name])


def test_harness() -> None:
    result = measure(2, 1)
    samples = result["samples"]
    assert len(samples) == 19
    for sample in samples:
        if sample.get("cache") == "populated":
            assert sample["handlers"] == []
            assert sample["artifact_bytes"] > 0
        elif "cache" in sample:
            assert sample["handlers"].count("source") == 1


def test_cache_preserves_rich_fixtures(tmp_path: Path) -> None:

    cache = TableCache(tmp_path)
    for name, raw in variants().items():
        value = Locations.model_validate(raw)
        with cache.lock(name):
            cache.write_locations(name, value)
        assert_exact(cache.read_locations(name), value)
