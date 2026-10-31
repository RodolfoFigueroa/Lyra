import numpy as np
import pyarrow as pa
import pytest
import shapely
from lyra.sdk import Locations, location_artifacts
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import location_snapshot, locations_from_snapshot

from benchmarks.location_baseline.fixtures import assert_exact, payload
from tests.location_helpers import frame_snapshot, stored_locations
from tests.test_locations_values import GEOMETRIES


def mixed(rows: int) -> Locations:
    raw = payload(rows)
    for feature in raw["features"]:
        feature["properties"].setdefault("value", None)
        geometry = feature["geometry"]
        if geometry["type"] == "Point" and len(geometry["coordinates"]) == 4:
            geometry["coordinates"] = [-0.0, 2.0, 3.0]
    return stored_locations(raw)


@pytest.mark.parametrize("rows", [0, 12, 4101])
def test_bulk_export_preserves_exact_contents(rows: int) -> None:
    value = mixed(rows)
    frame = value.to_geodataframe()
    restored = locations_from_snapshot(
        frame_snapshot(
            frame,
            collection=value.collection,
            unit_type=value.unit_type,
            id_column="id",
        )
    )
    assert_exact(restored.to_dict(), value.to_dict())
    if rows:
        frame["geometry"] = frame.geometry.translate(xoff=90)
        frame.loc[0, "id"] = "changed"
        assert_exact(value.to_geodataframe()["id"].tolist(), list(value.ids))
        assert value.to_geodataframe().geometry.iloc[0].x == 1


@pytest.mark.parametrize("kind", ["MultiPoint", "MultiLineString", "MultiPolygon"])
def test_all_empty_multipart(kind: str) -> None:
    raw = payload(3)
    for feature in raw["features"]:
        feature["geometry"] = {"type": kind, "coordinates": []}
    frame = stored_locations(raw).to_geodataframe()
    assert frame.geom_type.tolist() == [kind] * 3
    assert frame.geometry.is_empty.all()


def test_sliced_chunked_and_restored_snapshot() -> None:
    value = mixed(36)
    source = location_snapshot(value)
    sliced = LocationSnapshot(
        pa.concat_tables([source.table.slice(3, 7), source.table.slice(15, 8)]),
        pa.concat_tables([source.geometry.slice(3, 7), source.geometry.slice(15, 8)]),
        source.scope,
    )
    restored = location_artifacts.decode(location_artifacts.encode(sliced))
    result = locations_from_snapshot(restored).to_geodataframe()
    expected = value.to_geodataframe().iloc[[*range(3, 10), *range(15, 23)]]
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
    with pytest.raises(ValueError, match="dimensions"):
        stored_locations(raw).to_geodataframe()


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
    value = stored_locations(raw)
    result = value.to_geodataframe()
    assert not result.geometry.is_valid.all()
    restored = locations_from_snapshot(
        frame_snapshot(
            result,
            collection=value.collection,
            unit_type=value.unit_type,
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
    value = stored_locations(raw)
    result = value.to_geodataframe()
    restored = locations_from_snapshot(
        frame_snapshot(
            result,
            collection=value.collection,
            unit_type=value.unit_type,
            id_column="id",
        )
    )
    assert_exact(restored.to_dict(), value.to_dict())


def test_lossy_constructor_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = shapely.from_ragged_array

    def altered(
        kind: shapely.GeometryType,
        coordinates: np.ndarray,
        offsets: tuple[np.ndarray, ...],
    ) -> np.ndarray:
        return original(kind, coordinates + 1, offsets)

    monkeypatch.setattr(shapely, "from_ragged_array", altered)
    with pytest.raises(ValueError, match="structure"):
        mixed(12).to_geodataframe()
