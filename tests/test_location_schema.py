"""Native location validation preserves declared types and producer ownership."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.config import config_context
from pandera.engines.geopandas_engine import Geometry

from lyra.contracts.errors import MetricResultError
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.output_schema import OutputSchema
from test_support.location_schema import (
    Places,
    column,
    contract,
    declaration,
    locations,
)


@pytest.mark.parametrize(
    "schema", [Places, declaration(count=column("Int64", nullable=True))]
)
def test_compiled_metadata_and_source_schema_are_independent(
    schema: OutputSchema,
) -> None:
    compiled = contract(schema)
    source = locations({"count": 1}, {"count": None})
    pd.testing.assert_frame_equal(compiled.prepare_frame(source), source)
    assert compiled.metadata.id_column == "key"
    assert compiled.metadata.output.geometry == "geometry"
    assert compiled.metadata.output.columns[-1].nullable
    if isinstance(schema, pa.DataFrameSchema):
        schema.columns.clear()
    pd.testing.assert_frame_equal(compiled.prepare_frame(source), source)


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        ("int64", [1, 2]),
        ("Int64", [1, None]),
        ("float64", [1.5, None]),
        ("Float64", [1.5, None]),
        ("bool", [True, False]),
        ("boolean", [True, None]),
        ("str", ["a", None]),
        ("string", ["a", None]),
    ],
)
def test_native_types_and_empty_frames(dtype: str, values: list) -> None:
    compiled = contract(declaration(value=column(dtype, nullable=True)))
    source = locations({}, {})
    source["value"] = pd.Series(
        values, dtype=compiled.schema.columns["value"].dtype.type
    )
    for frame in [source, source.iloc[:0], compiled.empty_frame("EPSG:4326")]:
        result = compiled.prepare_frame(frame)
        assert result["value"].dtype == source["value"].dtype
        assert result.crs == source.crs
        pd.testing.assert_frame_equal(result, frame.reset_index(drop=True))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate_column",
        "duplicate_id",
        "empty_id",
        "geometry",
        "crs",
        "null",
    ],
)
def test_invalid_producer_frame(mutation: str) -> None:
    source = locations({"count": 1}, {"count": 2})
    alter_frame(source, mutation)
    with pytest.raises((ValueError, TypeError)):
        contract(declaration(count=column(int))).prepare_frame(source)


def test_requires_declared_dtype_and_crs() -> None:
    with pytest.raises(MetricResultError):
        contract(declaration(count=column(int))).prepare_frame(
            locations({"count": "2"})
        )
    schema = declaration()
    schema.columns["geometry"] = column(Geometry(crs=27700))
    with pytest.raises(ValueError, match="CRS"):
        contract(schema).prepare_frame(locations({}))


def test_checks_run_for_empty_inputs_and_despite_global_settings() -> None:
    schema = declaration(count=column(int, checks=pa.Check.ge(0)))
    schema.checks = [pa.Check(lambda frame: len(frame) > 0, name="nonempty")]
    with config_context(validation_enabled=False):
        for frame in [locations({"count": -1}), locations({"count": -1}).iloc[:0]]:
            with pytest.raises(MetricResultError):
                contract(schema).prepare_frame(frame, lazy=True)


@pytest.mark.parametrize(
    ("id_column", "geometry_column"),
    [
        ("absent", "geometry"),
        ("key", "absent"),
        ("geometry", "geometry"),
        ("geometry", "key"),
    ],
)
def test_structural_columns_declared(id_column: str, geometry_column: str) -> None:
    with pytest.raises(ValueError, match="distinct ID and geometry"):
        LocationOutputSchema(
            declaration(), id_column=id_column, geometry_column=geometry_column
        )


def alter_frame(frame: gpd.GeoDataFrame, mutation: str) -> bool:
    if mutation == "missing":
        del frame["count"]
    elif mutation == "extra":
        frame["extra"] = 1
    elif mutation == "duplicate_column":
        frame.columns = ["key", "geometry", "key"]
    elif mutation == "duplicate_id":
        frame["key"] = "same"
    elif mutation == "empty_id":
        frame.loc[0, "key"] = ""
    elif mutation == "geometry":
        frame.loc[0, "geometry"] = None
    elif mutation == "crs":
        frame.geometry = frame.geometry.set_crs(3857, allow_override=True)
    else:
        frame["count"] = pd.Series([None, 2], dtype="Int64")
    return True
