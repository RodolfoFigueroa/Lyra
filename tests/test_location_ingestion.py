"""Single-pass producer construction and exact bulk geometry ingestion."""

from typing import ClassVar, cast
from unittest.mock import Mock, patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest
import shapely
from lyra.sdk import (
    IOConfig,
    LocationCollection,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    PluginDefinition,
    content_fingerprints,
    geometry_buffers,
    geometry_shapely,
    location_operation,
)
from lyra.sdk.errors import MetricResultError
from lyra.sdk.location_resolution import resolve_locations
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from lyra.sdk.models.location import LocationSelection, location_fingerprint
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import mapping

from benchmarks.location_baseline.ingestion import measure
from tests.location_helpers import output_schema


def source() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["b", "a"], "count": [1, 2]},
        geometry=shapely.points([1, 2], [3, 4]),
        crs=4326,
    )


def test_frame_build_is_owned() -> None:
    result = source()
    result.index = [90, 80]
    schema = output_schema(count=int)
    with pytest.raises(ValueError, match="collection"):
        schema.build(result, collection="invalid", unit_type="lot")
    value = schema.build(result, collection="test:units", unit_type="lot")
    expected = value.model_dump()
    result.loc[90, "count"] = 99
    result.geometry = result.geometry.translate(xoff=100)
    exported = value.to_geodataframe()
    exported.loc[0, "count"] = 88
    assert value.model_dump() == expected
    assert value.ids == ["b", "a"]
    assert exported.index.tolist() == [0, 1]


@pytest.mark.parametrize("boundary", ["standalone", "operation", "provider"])
def test_fresh_boundaries_encode_and_hash_once(boundary: str) -> None:
    result = source()
    schema = output_schema(count=int)

    @location_operation(
        name="make",
        description="Frame",
        output=schema,
        collection="test:units",
        unit_type="lot",
    )
    def make() -> gpd.GeoDataFrame:
        return result

    plugin = PluginDefinition(location_operations=[make])

    class Provider(LocationProvider[IOConfig]):
        collections = (
            LocationCollection(name="test:units", unit_types=("lot",), crs="EPSG:4326"),
        )
        outputs: ClassVar = {("test:units", "lot"): schema}

        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            assert self.outputs[collection, unit_type] is schema
            assert ids == ["a", "b"]
            return result

    selected = SelectedLocationProvider(
        "test",
        LocationProviderManifest(
            resource="locations", collections=Provider.collections
        ),
    )
    with (
        patch.object(Locations, "to_geodataframe", side_effect=AssertionError),
        patch.object(LocationSnapshot, "to_features", side_effect=AssertionError),
        patch.object(geometry_buffers, "encode", side_effect=AssertionError),
        patch(
            "lyra.sdk.geometry_buffers._ADAPTER.validate_python",
            side_effect=AssertionError,
        ),
        patch("shapely.geometry.geo.mapping", side_effect=AssertionError),
        patch(
            "lyra.sdk.location_frames.ingest_geometries",
            wraps=geometry_shapely.ingest_geometries,
        ) as encode,
        patch.object(
            content_fingerprints, "compute", wraps=content_fingerprints.compute
        ) as compute,
        patch.object(
            LocationSnapshot,
            "__init__",
            autospec=True,
            side_effect=LocationSnapshot.__init__,
        ) as construct,
    ):
        if boundary == "standalone":
            value = schema.build(result, collection="test:units", unit_type="lot")
        elif boundary == "operation":
            value = plugin.invoke_operation(
                plugin.prepare_resolved_invocation("make", {}), {}, Mock()
            )
        else:
            value = resolve_locations(
                {
                    "source": LocationSelection(
                        collection="test:units",
                        unit_type="lot",
                        ids=["a", "b"],
                    )
                },
                selected=selected,
                resources={"locations": Provider},
            )["source"]
        assert encode.call_count == compute.call_count == construct.call_count == 1
        assert value.ids == (["a", "b"] if boundary == "provider" else ["b", "a"])


@pytest.mark.parametrize("wrong", [None, pd.DataFrame(), {}])
def test_wrong_producer_types(wrong: object) -> None:
    with pytest.raises(TypeError, match="GeoDataFrame"):
        output_schema().build(
            cast("gpd.GeoDataFrame", wrong), collection="test:units", unit_type="lot"
        )


def test_existing_snapshot_is_not_a_producer_result() -> None:
    schema = output_schema(count=int)
    with pytest.raises(TypeError, match="GeoDataFrame"):
        schema.build(
            cast(
                "gpd.GeoDataFrame",
                schema.build(source(), collection="test:units", unit_type="lot"),
            ),
            collection="test:units",
            unit_type="lot",
        )


@pytest.mark.parametrize("coerce", [False, True])
def test_native_dtype_contract_and_declared_coercion(*, coerce: bool) -> None:
    result = source()
    result["count"] = ["1", "2"]
    schema = output_schema(count=int)
    schema.schema.columns["count"].coerce = coerce
    if coerce:
        assert (
            schema.build(result, collection="test:units", unit_type="lot")
            .features[0]
            .properties["count"]
            == 1
        )
        assert result["count"].tolist() == ["1", "2"]
    else:
        with pytest.raises(ValueError, match="count"):
            schema.build(result, collection="test:units", unit_type="lot")


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

    schema.schema.checks = [pa.Check(alter, name="alter")]
    with pytest.raises((ValueError, TypeError)):
        schema.build(result, collection="test:units", unit_type="lot")
    pd.testing.assert_frame_equal(result, before)


def test_empty_custom_columns_and_required_presence() -> None:
    schema = LocationOutputSchema(
        pa.DataFrameSchema(
            {
                name: pa.Column(dtype, description=name, metadata={"unit": None})
                for name, dtype in {"key": str, "shape": Geometry, "count": int}.items()
            }
        ),
        id_column="key",
        geometry_column="shape",
    )
    schema.schema.ordered = True
    empty = schema.empty_frame("EPSG:4326")
    assert schema.build(empty, collection="test:units", unit_type="lot").ids == []
    with pytest.raises(ValueError, match="declared columns"):
        schema.build(
            empty.drop(columns="count"),
            collection="test:units",
            unit_type="lot",
        )


def test_lazy_schema_reports_multiple_failures() -> None:
    schema = output_schema(count=int)
    schema.schema.columns["count"].checks = [pa.Check.gt(10), pa.Check.lt(0)]
    with pytest.raises(MetricResultError) as caught:
        schema.build(source(), lazy=True, collection="test:units", unit_type="lot")
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
    value = Locations.from_geodataframe(
        frame, collection="test:units", unit_type="shape", id_column="id"
    )
    reference = Locations.model_validate(
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
    assert location_fingerprint(value) == location_fingerprint(reference)


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
        "MULTILINESTRING (EMPTY)",
    ],
)
def test_unsupported_geometry_is_rejected(wkt: str) -> None:
    with pytest.raises(ValueError, match=r"[Gg]eometry|coordinates"):
        geometry_shapely.ingest_geometries(np.asarray([shapely.from_wkt(wkt)]))


def test_lossy_bulk_extraction_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = shapely.to_ragged_array

    def altered(geometries: np.ndarray, *, include_z: bool, include_m: bool) -> tuple:
        kind, coordinates, offsets = original(
            geometries, include_z=include_z, include_m=include_m
        )
        return kind, coordinates + 1, offsets

    monkeypatch.setattr(shapely, "to_ragged_array", altered)
    with pytest.raises(ValueError, match="loss"):
        geometry_shapely.ingest_geometries(np.asarray([shapely.Point(1, 2)]))


@pytest.mark.parametrize("operation", ["import", "operation", "provider"])
def test_offline_ingestion_harness(operation: str) -> None:
    record = measure(8, "mixed", operation, 1)
    assert record["parity"]
    assert len(record["seconds"]) == 1


def test_typed_null_and_absent_property_are_distinct() -> None:
    result = source()
    result["count"] = pd.Series([1, pd.NA], dtype="Int64")
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    assert schema.build(result, collection="test:units", unit_type="lot").features[
        1
    ].properties == {"count": None}
    result["count"] = pd.Series([1, pd.NA], dtype=object)
    schema.schema.columns["count"].coerce = True
    with pytest.raises(ValueError, match="Missing required property"):
        schema.build(result, collection="test:units", unit_type="lot")


def test_valid_schema_geometry_changes_are_captured() -> None:
    result = source()
    schema = output_schema(count=int)

    def translate(frame: gpd.GeoDataFrame) -> bool:
        frame.geometry = frame.geometry.translate(xoff=10)
        return True

    schema.schema.checks = [pa.Check(translate, name="translate")]
    value = schema.build(result, collection="test:units", unit_type="lot")
    assert value.to_geodataframe().geometry.x.tolist() == [11, 12]
    assert result.geometry.x.tolist() == [1, 2]


def test_missing_geometry_is_rejected() -> None:
    result = source()
    result.loc[0, "geometry"] = None
    with pytest.raises(ValueError, match="geometry"):
        output_schema(count=int).build(result, collection="test:units", unit_type="lot")
