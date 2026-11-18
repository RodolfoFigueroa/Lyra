from copy import deepcopy

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pandera.api.pandas.model_config import BaseConfig
from pandera.typing import Series
from shapely.geometry import Point, Polygon

from lyra.contracts.errors import MetricResultError
from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.sdk.storage_schema import describe, prepare_native
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration, frame


@pytest.mark.parametrize("values", [[], [None, None], [-(2**63), 2**63 - 1]])
def test_integer_range_empty_and_null(values: list[int | None]) -> None:
    schema = pa.DataFrameSchema({"value": column("Int64", nullable=True)}, strict=True)
    source = pd.DataFrame({"value": pd.Series(values, dtype="Int64")})
    owned, layout = prepare_native(source, schema)
    cells = rows_from_validated(owned, layout)
    assert cells == [(value,) for value in values]
    pd.testing.assert_frame_equal(frame_from_rows(cells, layout), source)


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        ("string", ["a", None]),
        ("boolean", [True, None]),
        ("Float64", [1.25, None]),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_scalar_logical_types(dtype: str, values: list, *, empty: bool) -> None:
    schema = pa.DataFrameSchema({"value": column(dtype, nullable=True)}, strict=True)
    source = pd.DataFrame({"value": pd.Series([] if empty else values, dtype=dtype)})
    owned, layout = prepare_native(source, schema)
    restored = frame_from_rows(rows_from_validated(owned, layout), layout)
    pd.testing.assert_frame_equal(restored, source)
    assert restored.value.dtype == pd.Series([], dtype=dtype).dtype


def test_model_description_and_owned_metadata() -> None:
    class StrictConfig(BaseConfig):
        strict = True

    class Table(pa.DataFrameModel):
        Config: type[BaseConfig] = StrictConfig
        value: Series[pd.Int64Dtype] = pa.Field(metadata={"unit": None})

    assert describe(Table).pandas_dtypes == ("Int64",)
    schema = declaration()
    layout = describe(schema)
    original = deepcopy(layout)
    schema.columns["count"].description = "Changed"
    schema.columns["count"].metadata["unit"] = "m"
    assert layout == original
    assert layout.geometry == "geometry"
    assert layout.crs == "EPSG:4326"


@pytest.mark.parametrize(
    "dtype", ["category", "datetime64[ns]", "object", "uint64", "int8"]
)
def test_unknown_declarations_fail_without_rows(dtype: str) -> None:
    schema = pa.DataFrameSchema({"value": column(dtype)}, strict=True)
    with pytest.raises(ValueError, match="Unsupported"):
        describe(schema)
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_native(pd.DataFrame(), schema)


@pytest.mark.parametrize("empty", [False, True])
def test_wkb_and_declared_crs(*, empty: bool) -> None:
    source = frame()
    if empty:
        source = source.iloc[:0]
    owned, layout = prepare_native(source, declaration())
    cells = rows_from_validated(owned, layout)
    restored = frame_from_rows(cells, layout)
    assert isinstance(restored, gpd.GeoDataFrame)
    assert isinstance(owned, gpd.GeoDataFrame)
    assert_geodataframe_equal(restored, owned.astype({"location_id": "string"}))
    if cells:
        assert isinstance(cells[0][layout.columns.index("geometry")], bytes)


def test_producer_callback_and_caller_references_are_isolated() -> None:
    retained = []

    def check(value: pd.DataFrame) -> bool:
        retained.append(value)
        return True

    schema = pa.DataFrameSchema(
        {"value": column("Int64")},
        strict=True,
        checks=pa.Check(check, name="retain_reference"),
    )
    source = pd.DataFrame({"value": pd.Series([1], dtype="Int64")})
    source.attrs = {"scope": {"ids": ["untrusted"]}}
    owned, layout = prepare_native(source, schema)
    assert len(retained) == 1
    source.loc[0, "value"] = 2
    retained[0].loc[0, "value"] = 3
    retained[0].attrs["scope"]["ids"].clear()
    assert owned.attrs["scope"]["ids"] == ["untrusted"]
    cells = rows_from_validated(owned, layout)
    working = frame_from_rows(cells, layout)
    working.loc[0, "value"] = 4
    assert rows_from_validated(owned, layout) == [(1,)]
    assert frame_from_rows(cells, layout).value.tolist() == [1]
    assert not frame_from_rows(cells, layout).attrs
    assert len(retained) == 1


@pytest.mark.parametrize("values", [[float("inf")], [-float("inf")]])
def test_nonfinite_values_rejected(values: list[float]) -> None:
    schema = pa.DataFrameSchema({"value": column("Float64")}, strict=True)
    with pytest.raises(MetricResultError, match="infinity"):
        prepare_native(
            pd.DataFrame({"value": pd.Series(values, dtype="Float64")}), schema
        )


@pytest.mark.parametrize("values", [[[1]], [{"nested": 1}], [None]])
def test_nested_and_nonnullable_cells_rejected(values: list) -> None:
    schema = pa.DataFrameSchema({"value": column("str")}, strict=True)
    with pytest.raises(MetricResultError):
        prepare_native(pd.DataFrame({"value": values}), schema)


@pytest.mark.parametrize("columns", [["other"], ["value", "extra"], ["value", "value"]])
def test_exact_columns(columns: list[str]) -> None:
    schema = pa.DataFrameSchema({"value": column("Int64")}, strict=True)
    with pytest.raises(MetricResultError, match="declared columns"):
        prepare_native(pd.DataFrame(columns=columns), schema)


def test_invalid_geometry_and_wrong_crs() -> None:
    source = frame()
    source.geometry = [Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])] * len(source)
    with pytest.raises(MetricResultError):
        prepare_native(source, declaration())
    source.geometry = [Point(1, 2)] * len(source)
    source = source.set_crs(3857, allow_override=True)
    with pytest.raises(MetricResultError, match="CRS"):
        prepare_native(source, declaration())


@pytest.mark.parametrize("dtype", ["string", "boolean", "Float64"])
def test_all_null_scalars(dtype: str) -> None:
    schema = pa.DataFrameSchema({"value": column(dtype, nullable=True)}, strict=True)
    source = pd.DataFrame({"value": pd.Series([None, None], dtype=dtype)})
    owned, layout = prepare_native(source, schema)
    cells = rows_from_validated(owned, layout)
    assert cells == [(None,), (None,)]
    pd.testing.assert_frame_equal(frame_from_rows(cells, layout), source)


def test_nullable_geometry_and_coercion() -> None:
    schema = declaration()
    schema.columns["geometry"].nullable = True
    schema.columns["count"].coerce = True
    source = frame()
    source.geometry = gpd.GeoSeries([None, None], crs=source.crs)
    source["count"] = ["1", "2"]
    owned, layout = prepare_native(source, schema)
    cells = rows_from_validated(owned, layout)
    assert all(row[layout.columns.index("geometry")] is None for row in cells)
    restored = frame_from_rows(cells, layout)
    assert isinstance(restored, gpd.GeoDataFrame)
    assert restored.crs == source.crs
    assert restored.geometry.isna().all()
    assert restored["count"].tolist() == [1, 2]
    assert restored["count"].dtype == pd.Int64Dtype()
    assert source["count"].tolist() == ["1", "2"]


def test_zero_column_row_cardinality() -> None:
    schema = pa.DataFrameSchema({}, strict=True)
    source = pd.DataFrame(index=range(3))
    owned, layout = prepare_native(source, schema)
    assert rows_from_validated(owned, layout) == [(), (), ()]
    pd.testing.assert_frame_equal(frame_from_rows([(), (), ()], layout), source)
