import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
from shapely.geometry import Point

from lyra.data import arrow_conversion
from lyra.sdk import storage_schema
from test_support.location_helpers import output_schema


@pytest.mark.parametrize("rows", [0, 3])
def test_native_zero_columns(rows: int) -> None:
    frame = pd.DataFrame(index=range(rows))
    table = arrow_conversion.to_arrow(frame, pa.schema([]))
    assert table.num_rows == rows
    assert arrow_conversion.from_arrow(table).shape == (rows, 0)
    assert arrow_conversion.from_arrow(table).index.equals(pd.RangeIndex(rows))


def test_native_nullability_and_finite_values() -> None:
    schema = pa.schema([pa.field("value", pa.float64(), nullable=False)])
    for value, message in [(None, "nullable"), (float("inf"), "infinity")]:
        with pytest.raises(ValueError, match=message):
            arrow_conversion.to_arrow(pd.DataFrame({"value": [value]}), schema)


def test_adapter_checks_once_and_preserves_coercion() -> None:
    calls = []

    def check(frame: pd.DataFrame) -> bool:
        calls.append(len(frame))
        return True

    declaration = pandera.DataFrameSchema(
        {"value": pandera.Column("int8", coerce=True)},
        strict=True,
        checks=pandera.Check(check),
    )
    source = pd.DataFrame({"value": ["2"]})
    prepared = storage_schema.prepare(source, declaration)
    table = arrow_conversion.to_arrow(
        prepared.frame, prepared.schema, columns=prepared.columns
    )
    assert calls == [1]
    assert arrow_conversion.from_arrow(table).value.dtype == "int8"
    assert source.value.tolist() == ["2"]


def test_location_build_and_revalidation_check_once() -> None:
    calls = []

    def check(frame: pd.DataFrame) -> bool:
        calls.append(len(frame))
        return True

    schema = output_schema(value="Int64")
    schema.schema.checks = [pandera.Check(check, name="count_checks")]
    frame = gpd.GeoDataFrame(
        {"id": ["a"], "value": pd.Series([2], dtype="Int64")},
        geometry=[Point(1, 2)],
        crs=4326,
    )
    value = schema.prepare_frame(frame)
    assert calls == [1]
    schema.prepare_frame(value)
    assert calls == [1, 1]


def test_native_constructor_rejects_extra_columns() -> None:
    with pytest.raises(ValueError, match="declared columns"):
        arrow_conversion.to_arrow(
            pd.DataFrame({"value": [1], "extra": [2]}),
            pa.schema([("value", pa.int64())]),
        )
