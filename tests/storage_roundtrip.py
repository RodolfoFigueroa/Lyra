from itertools import product
from math import copysign
from pathlib import Path

import pandas as pd
from pandera.errors import SchemaError

from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.sdk.storage_schema import prepare_native
from test_support.relational_workload import read_outputs, write_outputs
from test_support.workloads import schema as schema_for
from tests.frame_assertions import (
    assert_equal as assert_native_equal,
)
from tests.frame_assertions import (
    assert_logical_equal,
)
from tests.storage_data import native_declarations as declarations
from tests.storage_data import native_frame as frame


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
    if path == "metric-cache":
        write_outputs(directory, {"result": expected})
        return read_outputs(directory)["result"]
    prepared, description = prepare_native(expected, schema_for(expected))
    return frame_from_rows(rows_from_validated(prepared, description), description)


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
            actual = roundtrip(
                expected, directory / f"{spatial}-{state}-{categories}", path
            )
            actual = actual[list(schema.columns)]
            schema.validate(actual)
            assert_logical_equal(
                actual, expected[list(schema.columns)].reset_index(drop=True)
            )
            error = None
        except (AssertionError, ValueError, TypeError, SchemaError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        records.append(
            {"spatial": spatial, "state": state, "path": path, "error": error}
        )
    return records
