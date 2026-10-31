from pathlib import Path

import pytest
from lyra.sdk.cache import TableCache
from pydantic import ValidationError

from tests.location_checks import rich_payload, rich_schema
from tests.location_data import (
    assert_exact,
    exact,
    invalid_inputs,
    payload,
    variants,
)
from tests.location_graph import workload
from tests.location_helpers import fixture_locations, stored_locations


def test_fixtures() -> None:
    for raw in variants().values():
        value = stored_locations(raw)
        assert_exact(value, raw)
    for raw in invalid_inputs().values():
        with pytest.raises(ValidationError):
            stored_locations(raw)


def test_fixture_scalar_coverage() -> None:
    properties = [feature["properties"] for feature in payload()["features"]]
    values = {exact(item["value"]) for item in properties if "value" in item}
    assert values == {exact(value) for value in (0, 1, 2, 2**60 + 1, 4, None)}
    assert all(set(row) == {"value"} for row in properties)


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


@pytest.mark.parametrize("shape", ["chain", "fanout", "shared"])
def test_graph_cache_reuses_results(tmp_path: Path, shape: str) -> None:
    value = fixture_locations(rich_payload(2), schema=rich_schema())
    runner, calls = workload(value, shape, tmp_path, output=rich_schema())
    assert runner.run("root", {})["root"]["count"].tolist() == [4]
    assert calls.count("source") == 1
    calls.clear()
    assert runner.run("root", {})["root"]["count"].tolist() == [4]
    assert calls == []


def test_cache_preserves_rich_fixtures(tmp_path: Path) -> None:

    cache = TableCache(tmp_path)
    for name, raw in variants().items():
        value = stored_locations(raw)
        with cache.lock(name):
            cache.write_locations(name, value)
        assert_exact(cache.read_locations(name), value)
