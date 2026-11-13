from typing import Literal, cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.config import config_context
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point, Polygon

from lyra.contracts.errors import MetricResultError
from lyra.sdk.provider_schema import ProviderSchema
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration, frame


@pytest.mark.parametrize("strict", [False, "filter"])
def test_requires_explicit_strictness(*, strict: bool | str) -> None:
    schema = declaration()
    schema.strict = strict
    with pytest.raises(ValueError, match="strict=True"):
        ProviderSchema(schema)


@pytest.mark.parametrize(
    ("name", "attribute", "value"),
    [
        ("count", "required", False),
        ("count", "regex", True),
        ("count", "description", None),
        ("count", "metadata", {}),
        ("location_id", "dtype", int),
        ("location_id", "nullable", True),
        ("location_id", "unique", False),
        ("location_id", "checks", []),
        ("geometry", "nullable", True),
        ("geometry", "dtype", Geometry),
        ("geometry", "dtype", str),
        ("geometry", "checks", []),
    ],
)
def test_rejects_invalid_column_declarations(
    name: str, attribute: str, value: object
) -> None:
    schema = declaration()
    setattr(schema.columns[name], attribute, value)
    with pytest.raises(ValueError, match=r"columns|Column|location_id|geometry"):
        ProviderSchema(schema)


@pytest.mark.parametrize("name", ["location_id", "geometry"])
def test_requires_structural_columns(name: str) -> None:
    schema = declaration()
    del schema.columns[name]
    with pytest.raises(ValueError, match="distinct ID and geometry"):
        ProviderSchema(schema)


@pytest.mark.parametrize("name", ["source_location_id", "row_key"])
def test_reserves_structural_names(name: str) -> None:
    schema = declaration()
    schema.columns[name] = column(str)
    with pytest.raises(ValueError, match="reserved"):
        ProviderSchema(schema)


def test_rejects_automatic_missing_column_insertion() -> None:
    schema = declaration()
    schema.add_missing_columns = True
    with pytest.raises(ValueError, match="add columns"):
        ProviderSchema(schema)


@pytest.mark.parametrize("coerce", [False, True])
@pytest.mark.parametrize("ids", [[1, 2], [None, "b"], ["", "b"], ["a", "a"]])
def test_identity_is_checked_before_coercion(ids: list, *, coerce: bool) -> None:
    schema = declaration()
    schema.coerce = coerce
    data = frame()
    data["location_id"] = ids
    with pytest.raises(ValueError, match=r"string|unique"):
        ProviderSchema(schema).validate(data)


@pytest.mark.parametrize(
    "mutation",
    ["extra", "missing", "duplicate", "crs", "no_crs", "active", "type", "null"],
)
def test_rejects_invalid_native_frames(mutation: str) -> None:
    data = frame()
    match mutation:
        case "extra":
            data["extra"] = 1
        case "missing":
            data = data.drop(columns="count")
        case "duplicate":
            data = pd.concat([data, data[["count"]]], axis=1)
        case "crs":
            data = data.to_crs("EPSG:3857")
        case "no_crs":
            data = gpd.GeoDataFrame(data.to_dict("list"), geometry="geometry")
        case "active":
            data = data.rename_geometry("shape")
        case "type":
            data["count"] = ["one", "two"]
        case "null":
            data.loc[0, "geometry"] = None
    with pytest.raises((ValueError, MetricResultError)):
        ProviderSchema(declaration()).validate(data)


@pytest.mark.parametrize(
    "geometry", [Point(), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])]
)
def test_executes_geometry_checks(geometry: Point | Polygon) -> None:
    data = frame()
    data["geometry"] = gpd.GeoSeries([geometry, Point(1, 1)], crs=data.crs)
    with pytest.raises(MetricResultError):
        ProviderSchema(declaration()).validate(data)


@pytest.mark.parametrize("empty", [False, True])
def test_native_types_metadata_and_input_isolation(*, empty: bool) -> None:
    schema = declaration()
    compiled = ProviderSchema(schema)
    schema.columns.clear()
    data = frame()
    data["count"] = pd.Series([None, None], dtype="Int64")
    data.attrs["scope"] = "not authoritative"
    if empty:
        data = data.iloc[:0]
    original = data.copy(deep=True)
    result = compiled.validate(data)
    assert isinstance(result, gpd.GeoDataFrame)
    pd.testing.assert_frame_equal(result, data[list(compiled.schema.columns)])
    assert result.crs == data.crs
    assert result.active_geometry_name == "geometry"
    result["count"] = 99
    pd.testing.assert_frame_equal(data, original)
    assert compiled.metadata.geometry == "geometry"


@pytest.mark.parametrize("name", ["valid_geometry", "another_equivalent_check"])
def test_custom_check_names_do_not_determine_semantics(name: str) -> None:
    schema = declaration()
    schema.columns["geometry"].checks = [
        pa.Check(lambda s: (~s.is_empty) & s.is_valid, name=name)
    ]
    compiled = ProviderSchema(schema)
    with config_context(validation_enabled=False):
        assert len(compiled.validate(frame())) == 2
        data = frame()
        data["geometry"] = gpd.GeoSeries([Point(), Point(1, 1)], crs=data.crs)
        with pytest.raises(MetricResultError):
            compiled.validate(data, lazy=True)


def test_requires_a_native_geodataframe() -> None:
    with pytest.raises(TypeError, match="GeoDataFrame"):
        ProviderSchema(declaration()).validate(
            cast("gpd.GeoDataFrame", pd.DataFrame(frame()))
        )


@pytest.mark.parametrize("dtype", [str, "string", "string[pyarrow]"])
def test_supports_native_string_dtypes(
    dtype: Literal["string", "string[pyarrow]"] | type[str],
) -> None:
    schema = declaration()
    schema.columns["location_id"].dtype = dtype
    data = frame()
    data["location_id"] = data["location_id"].astype(dtype)
    result = ProviderSchema(schema).validate(data)
    assert result["location_id"].dtype == data["location_id"].dtype


def test_attribute_checks_and_nonnullability_are_enforced() -> None:
    schema = declaration()
    schema.columns["count"].nullable = False
    schema.columns["count"].checks = [pa.Check.ge(0)]
    compiled = ProviderSchema(schema)
    with pytest.raises(ValueError, match="not nullable"):
        compiled.validate(frame())
    data = frame()
    data["count"] = pd.Series([-1, 2], dtype="Int64")
    with pytest.raises(MetricResultError):
        compiled.validate(data)
