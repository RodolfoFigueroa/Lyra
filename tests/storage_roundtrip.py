"""Shared production regression fixtures and assertions."""

from itertools import product
from math import copysign
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
from lyra.sdk import LocationOutputSchema, Locations
from lyra.sdk.cache import TableCache
from pandera.errors import SchemaError

from tests.frame_assertions import (
    assert_equal as assert_native_equal,
)
from tests.storage_data import declarations, frame


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
