"""Producer validation, deferred materialization, and exact geometry ingestion."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
import shapely
from shapely.geometry import mapping

from lyra.contracts.errors import MetricResultError
from lyra.data import geometry_buffers, geometry_shapely
from test_support.location_helpers import (
    output_schema,
    stored_locations,
)


def source() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["b", "a"], "count": [1, 2]},
        geometry=shapely.points([1, 2], [3, 4]),
        crs=4326,
    )


@pytest.mark.parametrize(
    "mutation", ["id", "geometry", "crs", "absent", "extra", "infinity"]
)
def test_post_schema_invariants_and_source_isolation(mutation: str) -> None:
    result = source()
    before = result.copy(deep=True)
    schema = output_schema(count=int)

    def alter(frame: gpd.GeoDataFrame) -> bool:
        if mutation == "id":
            frame["id"] = "duplicate"
        elif mutation == "geometry":
            frame["geometry"] = None
        elif mutation == "crs":
            frame.set_crs(27700, allow_override=True, inplace=True)
        elif mutation == "absent":
            frame["count"] = pd.Series([pd.NA, 2], dtype=object)
        elif mutation == "extra":
            frame["extra"] = 1
        else:
            frame["count"] = float("inf")
        return True

    schema.schema.checks = [pandera.Check(alter, name="alter")]
    with pytest.raises((ValueError, TypeError)):
        schema.prepare_frame(result)
    pd.testing.assert_frame_equal(result, before)


def test_lazy_schema_reports_multiple_failures() -> None:
    schema = output_schema(count=int)
    schema.schema.columns["count"].checks = [pandera.Check.gt(10), pandera.Check.lt(0)]
    with pytest.raises(MetricResultError) as caught:
        schema.prepare_frame(source(), lazy=True)
    assert len(caught.value.failures) == 2


@pytest.mark.parametrize("rows", [4095, 4096, 4097])
def test_bulk_batches_match_model_codec(rows: int) -> None:
    geometries = [
        shapely.from_wkt(wkt)
        for wkt in [
            "POINT (-0.0 2)",
            "POINT Z (1 2 3)",
            "MULTIPOINT ((1 2), (3 4))",
            "MULTIPOINT Z ((1 2 3), (3 4 5))",
            "LINESTRING (0 0, 1 1)",
            "LINESTRING Z (0 0 1, 1 1 2)",
            "MULTILINESTRING ((0 0, 1 1))",
            "MULTILINESTRING Z ((0 0 1, 1 1 2))",
            "POLYGON ((0 0, 4 0, 4 4, 0 0), (1 1, 2 1, 2 2, 1 1))",
            "POLYGON Z ((0 0 1, 1 1 2, 0 1 3, 0 0 1))",
            "MULTIPOLYGON (((0 0, 1 1, 0 1, 0 0)))",
            "MULTIPOLYGON Z (((0 0 1, 1 1 2, 0 1 3, 0 0 1)))",
            "MULTIPOINT EMPTY",
            "MULTILINESTRING EMPTY",
            "MULTIPOLYGON EMPTY",
            "POLYGON ((0 0, 2 2, 0 2, 2 0, 0 0))",
        ]
    ]
    shapes = np.asarray([geometries[i % len(geometries)] for i in range(rows)])
    raw = geometry_buffers.encode(map(mapping, shapes))
    bulk = geometry_shapely.ingest_geometries(shapes)
    geometry_buffers.validate(bulk)
    assert bulk.equals(raw)
    frame = gpd.GeoDataFrame(
        {"id": [str(i) for i in range(rows)]}, geometry=shapes, crs=4326
    )
    value = output_schema().prepare_frame(frame)
    reference = stored_locations(
        {
            "collection": "test:units",
            "unit_type": "shape",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(i),
                    "geometry": mapping(shape),
                    "properties": {},
                }
                for i, shape in enumerate(shapes)
            ],
        }
    )
    pd.testing.assert_frame_equal(value, reference)


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT EMPTY",
        "LINESTRING EMPTY",
        "POLYGON EMPTY",
        "GEOMETRYCOLLECTION EMPTY",
        "POINT M (1 2 3)",
        "POINT ZM (1 2 3 4)",
        "POINT (Infinity 2)",
        "MULTIPOINT Z ((1 2 3), (4 5 NaN))",
        "MULTIPOINT (EMPTY)",
        "MULTIPOINT (EMPTY, (1 2))",
        "MULTILINESTRING (EMPTY)",
        "MULTILINESTRING (EMPTY, (1 2, 3 4))",
        "MULTIPOLYGON (EMPTY)",
        "MULTIPOLYGON (EMPTY, ((0 0, 1 0, 1 1, 0 0)))",
    ],
)
def test_unsupported_geometry_is_rejected(wkt: str) -> None:
    shapes = np.asarray([shapely.from_wkt(wkt)])
    with pytest.raises(ValueError, match=r"[Gg]eometry|coordinates"):
        geometry_shapely.ingest_geometries(shapes)
    table = pa.Table.from_arrays(
        [pa.array(shapely.to_wkb(shapes), type=pa.binary())],
        schema=geometry_shapely.SCHEMA,
    )
    with pytest.raises(ValueError, match=r"[Gg]eometry|coordinates"):
        geometry_shapely.validate(table)


def test_valid_schema_geometry_changes_are_captured() -> None:
    result = source()
    schema = output_schema(count=int)

    def translate(frame: gpd.GeoDataFrame) -> bool:
        frame.geometry = frame.geometry.translate(xoff=10)
        return True

    schema.schema.checks = [pandera.Check(translate, name="translate")]
    value = schema.prepare_frame(result)
    assert value.copy(deep=True).geometry.x.tolist() == [11, 12]
    assert result.geometry.x.tolist() == [1, 2]


def test_missing_geometry_is_rejected() -> None:
    result = source()
    result.loc[0, "geometry"] = None
    with pytest.raises(ValueError, match="geometry"):
        output_schema(count=int).prepare_frame(result)
