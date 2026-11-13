import numpy as np
import pyarrow as pa
import pytest
import shapely

from lyra.data import location_artifacts
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.location_validation import validate_snapshot
from test_support.location_data import assert_exact, columns, payload
from test_support.location_helpers import (
    frame_snapshot,
    snapshot_frame,
)
from tests.geometry_fixtures import VALUE_GEOMETRIES as GEOMETRIES


def mixed(rows: int) -> LocationSnapshot:
    raw = payload(rows)
    for feature in raw["features"]:
        feature["properties"].setdefault("value", None)
    return LocationSnapshot(*columns(raw))


@pytest.mark.parametrize("rows", [0, 12, 4101])
def test_bulk_export_preserves_exact_contents(rows: int) -> None:
    value = mixed(rows)
    frame = snapshot_frame(value)
    restored = validate_snapshot(
        frame_snapshot(
            frame,
            collection=value.scope.collection,
            unit_type=value.scope.unit_type,
            id_column="id",
        )
    )
    assert_exact(restored.to_dict(), value.to_dict())
    if rows:
        frame["geometry"] = frame.geometry.translate(xoff=90)
        frame.loc[0, "id"] = "changed"
        assert_exact(
            snapshot_frame(value)["id"].tolist(), value.table["id"].to_pylist()
        )
        assert snapshot_frame(value).geometry.iloc[0].x == 1


@pytest.mark.parametrize("kind", ["MultiPoint", "MultiLineString", "MultiPolygon"])
def test_all_empty_multipart(kind: str) -> None:
    raw = payload(3)
    for feature in raw["features"]:
        feature["geometry"] = {"type": kind, "coordinates": []}
    frame = snapshot_frame(LocationSnapshot(*columns(raw)))
    assert frame.geom_type.tolist() == [kind] * 3
    assert frame.geometry.is_empty.all()


def test_sliced_chunked_and_restored_snapshot() -> None:
    value = mixed(36)
    source = value
    sliced = LocationSnapshot(
        pa.concat_tables([source.table.slice(3, 7), source.table.slice(15, 8)]),
        pa.concat_tables([source.geometry.slice(3, 7), source.geometry.slice(15, 8)]),
        source.scope,
    )
    restored = location_artifacts.decode(location_artifacts.encode(sliced))
    result = snapshot_frame(validate_snapshot(restored))
    expected = snapshot_frame(value).iloc[[*range(3, 10), *range(15, 23)]]
    assert result["id"].tolist() == expected["id"].tolist()
    assert np.array_equal(
        shapely.to_wkb(result.geometry), shapely.to_wkb(expected.geometry)
    )


@pytest.mark.parametrize(
    "coordinates",
    [[[0, 0], [1, 1, 1]], [[0, 0, 0, 0], [1, 1, 1, 1]]],
)
def test_unrepresentable_dimensions(coordinates: list) -> None:
    raw = payload(1)
    raw["features"][0]["geometry"] = {"type": "LineString", "coordinates": coordinates}
    with pytest.raises(ValueError, match=r"dimension|inhomogeneous|validation error"):
        snapshot_frame(LocationSnapshot(*columns(raw)))


def test_topology_and_coordinate_bits_are_preserved() -> None:
    raw = payload(1)
    raw["features"][0]["geometry"] = {
        "type": "Polygon",
        "coordinates": [
            [
                [-0.0, 0.0, 3.0],
                [2.0, 2.0, 3.0],
                [0.0, 2.0, 3.0],
                [2.0, 0.0, 3.0],
                [-0.0, 0.0, 3.0],
            ]
        ],
    }
    value = LocationSnapshot(*columns(raw))
    result = snapshot_frame(value)
    assert not result.geometry.is_valid.all()
    restored = validate_snapshot(
        frame_snapshot(
            result,
            collection=value.scope.collection,
            unit_type=value.scope.unit_type,
            id_column="id",
        )
    )
    assert_exact(restored.to_dict(), value.to_dict())


@pytest.mark.parametrize(
    "kind",
    ["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"],
)
def test_three_dimensional_geometry_types(kind: str) -> None:
    def add_z(coordinates: list) -> list:
        if coordinates and isinstance(coordinates[0], (int, float)):
            return [*coordinates[:2], 3.0]
        return [add_z(child) for child in coordinates]

    raw = payload(1)
    raw["features"][0]["geometry"] = {
        "type": kind,
        "coordinates": add_z(GEOMETRIES[kind]),
    }
    value = LocationSnapshot(*columns(raw))
    result = snapshot_frame(value)
    restored = validate_snapshot(
        frame_snapshot(
            result,
            collection=value.scope.collection,
            unit_type=value.scope.unit_type,
            id_column="id",
        )
    )
    assert_exact(restored.to_dict(), value.to_dict())
