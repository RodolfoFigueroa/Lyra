"""Integrated acceptance campaign: uv run python -m benchmarks.schema_storage.run."""

import json
import platform
from importlib.metadata import version
from itertools import product
from math import copysign
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
from lyra.sdk import LocationOutputSchema, Locations
from lyra.sdk.cache import TableCache
from pandera.errors import SchemaError

from benchmarks.location_baseline.acceptance import measure, rich_payload
from benchmarks.metric_representation.execution import (
    assert_equal as assert_native_equal,
)
from benchmarks.schema_storage.fixtures import declarations, frame
from tests.location_helpers import stored_locations


def assert_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Verify native fidelity, including the sign of floating-point zero.

    Raises:
        AssertionError: If contents, metadata, or zero signs differ.
    """
    assert_native_equal(actual, expected)
    for name in expected.select_dtypes(include="float").columns:
        for left, right in zip(actual[name], expected[name], strict=True):
            if (
                pd.notna(right)
                and right == 0
                and copysign(1, left) != copysign(1, right)
            ):
                message = f"Signed zero changed in {name}"
                raise AssertionError(message)


def roundtrip(expected: pd.DataFrame, directory: Path, path: str) -> pd.DataFrame:
    """Return the native result of a current storage boundary."""
    schema = declarations(spatial=isinstance(expected, gpd.GeoDataFrame))
    if "category" not in expected:
        schema = schema.remove_columns(["category"])
    if path == "metric-cache":
        cache = TableCache(directory)
        with cache.lock("fixture"):
            cache.write(
                "fixture",
                {"result": expected},
                {"result": schema},
            )
        return read_result(cache, "fixture", "result")
    source = gpd.GeoDataFrame(expected.copy())
    source["id"] = pd.array([str(i) for i in range(len(source))], dtype="str")
    declaration = schema
    declaration.columns["id"] = pandera.Column(str)
    for name, column in declaration.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    return (
        Locations.from_geodataframe(
            source,
            collection="benchmark:places",
            unit_type="unit",
            schema=LocationOutputSchema(declaration),
        )
        .to_geodataframe()
        .drop(columns="id")
    )


def read_result(cache: TableCache, key: str, name: str) -> pd.DataFrame:
    """Return a required cached output.

    Raises:
        ValueError: If publication or restoration failed.
    """
    outputs = cache.read(key)
    if outputs is None:
        message = "Cache result missing"
        raise ValueError(message)
    return outputs[name]


def fidelity(directory: Path, *, categories: bool = True) -> list[dict]:
    """Return fidelity observations outside performance comparisons."""
    records = []
    for spatial, state, path in product(
        (False, True),
        ("populated", "empty", "all-null", "shuffled", "indexed"),
        ("metric-cache", "location"),
    ):
        if path == "location" and not spatial:
            continue
        expected = frame(state, spatial=spatial)
        schema = declarations(spatial=spatial)
        if not categories:
            expected = expected.drop(columns="category")
            schema = schema.remove_columns(["category"])
        schema.validate(expected)
        try:
            actual = roundtrip(expected, directory, path)
            schema.validate(actual)
            assert_equal(actual, expected[list(schema.columns)].reset_index(drop=True))
            error = None
        except (AssertionError, ValueError, TypeError, SchemaError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        records.append(
            {"spatial": spatial, "state": state, "path": path, "error": error}
        )
    return records


def metric_samples(directory: Path) -> list[dict]:
    """Return timed cache boundaries after an exact fidelity gate."""
    expected = frame()
    schema = declarations()
    cache = TableCache(directory)
    with cache.lock("sample"):
        cache.write("sample", {"summary": expected}, {"summary": schema})
    assert_equal(read_result(cache, "sample", "summary"), expected)

    def read_validated() -> pd.DataFrame:
        return schema.validate(read_result(cache, "sample", "summary"))

    def write() -> None:
        with cache.lock("sample"):
            cache.write("sample", {"summary": expected}, {"summary": schema})

    records = []
    for repeat in range(3):
        for name, action in {
            "producer_validation": lambda: schema.validate(expected),
            "cache_write": write,
            "cache_read_validation": read_validated,
        }.items():
            start = perf_counter()
            action()
            records.append(
                {"operation": name, "repeat": repeat, "seconds": perf_counter() - start}
            )
    records.append(
        {
            "artifact_bytes": sum(
                p.stat().st_size for p in directory.rglob("*") if p.is_file()
            )
        }
    )
    return records


def main() -> None:
    """Write fidelity observations and integrated acceptance samples.

    Raises:
        ValueError: If selection changes ordered IDs.
    """
    with TemporaryDirectory() as temporary:
        path = Path(temporary)
        result = {
            "fidelity": fidelity(path / "fidelity"),
            "supported_fidelity": fidelity(path / "supported", categories=False),
            "metric": metric_samples(path / "metric"),
        }
        value = stored_locations(rich_payload(1000))
        selections = []
        for repeat in range(3):
            start = perf_counter()
            selected = value.select_ids(list(value.ids[:100]))
            elapsed = perf_counter() - start
            if list(selected.ids) != list(value.ids[:100]):
                message = "Selection changed IDs"
                raise ValueError(message)
            selections.append({"repeat": repeat, "seconds": elapsed})
        result["selection_100_of_1000"] = selections
        result["environment"] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "versions": {
                name: version(name)
                for name in ("pandas", "pandera", "pyarrow", "geopandas")
            },
        }
        result["location"] = {str(rows): measure(rows, 3) for rows in (100, 1000)}
        result["diagnostics"] = measure(1000, 1, diagnostic=True)
    Path(
        "schema-preserving-storage-plan/integrated_acceptance_results.json"
    ).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
