import json

import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk.arrow_conversion import arrow_schema, from_arrow, to_arrow
from pandera.engines import pandas_engine

from benchmarks.schema_storage.fixtures import declarations, frame


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
@pytest.mark.parametrize("spatial", [False, True])
def test_fixture_round_trip(state: str, *, spatial: bool) -> None:
    source = frame(state, spatial=spatial)
    original = source.copy(deep=True)
    schema = declarations(spatial=spatial)
    table = to_arrow(source, schema)
    expected = source.drop(columns="geometry", errors="ignore")
    expected = expected[list(declarations().columns)].reset_index(drop=True)
    pd.testing.assert_frame_equal(from_arrow(table), expected)
    pd.testing.assert_frame_equal(source, original)
    assert table.schema == arrow_schema(schema)
    assert json.loads(table.schema.metadata[b"pandas"])["index_columns"] == []
    for name in ("single", "double"):
        assert np.array_equal(
            np.signbit(from_arrow(table)[name].dropna()),
            np.signbit(expected[name].dropna()),
        )


@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float16",
        "float32",
        "float64",
        "bool",
        "boolean",
        "string",
        "datetime64[s]",
        "datetime64[ms]",
        "datetime64[us]",
        "datetime64[ns]",
    ],
)
def test_native_types(dtype: str) -> None:
    declared = (
        pandas_engine.DateTime(unit=dtype[11:-1])
        if dtype.startswith("datetime64")
        else dtype
    )
    schema = pa.DataFrameSchema({"value": pa.Column(declared)})
    for values in ([], [0, 1]):
        source = pd.DataFrame(
            {"value": pd.Series(values, dtype=schema.columns["value"].dtype.type)}
        )
        pd.testing.assert_frame_equal(from_arrow(to_arrow(source, schema)), source)


@pytest.mark.parametrize("value", [float("inf"), -float("inf")])
def test_infinity(value: float) -> None:
    with pytest.raises(ValueError, match="infinity"):
        to_arrow(
            pd.DataFrame({"x": [value]}), pa.DataFrameSchema({"x": pa.Column(float)})
        )


def test_coercion_is_only_declared() -> None:
    source = pd.DataFrame({"x": ["12"]})
    schema = pa.DataFrameSchema({"x": pa.Column("int8")})
    with pytest.raises(pa.errors.SchemaError):
        to_arrow(source, schema)
    schema.coerce = True
    assert from_arrow(to_arrow(source, schema))["x"].dtype == np.dtype("int8")
    assert source.iloc[0, 0] == "12"


@pytest.mark.parametrize("value", [128, -129, 1.5, "1"])
def test_incompatible_values(value: object) -> None:
    schema = pa.DataFrameSchema({"x": pa.Column("int8")})
    with pytest.raises(pa.errors.SchemaError):
        to_arrow(pd.DataFrame({"x": [value]}), schema)


@pytest.mark.parametrize("marker", [None, pd.NA, float("nan")])
def test_missing_markers(marker: object) -> None:
    source = pd.DataFrame({"x": pd.Series([marker], dtype="Float64")})
    schema = pa.DataFrameSchema({"x": pa.Column("Float64", nullable=True)})
    assert to_arrow(source, schema)["x"].null_count == 1
    schema.columns["x"].nullable = False
    with pytest.raises(pa.errors.SchemaError):
        to_arrow(source, schema)
