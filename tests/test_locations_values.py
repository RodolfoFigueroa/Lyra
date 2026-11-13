from typing import Any

import pytest
from pydantic import ValidationError
from pyproj.exceptions import CRSError

from lyra.contracts.geometry import Feature
from lyra.contracts.locations import LocationSelection
from lyra.data import geometry_buffers
from test_support.location_helpers import stored_locations
from tests.geometry_fixtures import VALUE_GEOMETRIES as GEOMETRIES


def payload() -> dict[str, Any]:
    return {
        "collection": "example:stations",
        "unit_type": "sensor",
        "crs": {"type": "name", "properties": {"name": "EPSG:27700"}},
        "features": [
            {
                "type": "Feature",
                "id": f"00{index}",
                "geometry": {"type": kind, "coordinates": coordinates},
                "properties": {
                    "label": "sensor",
                    "count": 1,
                    "reading": 2.5,
                    "active": True,
                    "missing": None,
                    "day": "2026-10-29",
                    "timestamp": "2026-10-29T12:00:00+00:00",
                },
            }
            for index, (kind, coordinates) in enumerate(GEOMETRIES.items())
        ],
    }


def test_order_scope_properties_and_geometries_round_trip() -> None:
    original = payload()
    value = stored_locations(original)
    assert value["id"].tolist() == [feature["id"] for feature in original["features"]]
    list(value["id"].tolist()).reverse()
    assert value["id"].tolist()[0] == "000"


def test_empty_values_and_selections_retain_scope() -> None:
    original = payload() | {"features": []}
    value = stored_locations(original)
    assert value["id"].tolist() == []
    selection = LocationSelection(provider=str(original["collection"]), ids=[])
    assert selection.ids == []


@pytest.mark.parametrize("identifier", [1, True, b"id", "", None])
def test_invalid_ids(identifier: object) -> None:
    original = payload()
    original["features"][0]["id"] = identifier
    with pytest.raises(ValueError, match="id"):
        stored_locations(original)


def test_duplicate_ids() -> None:
    original = payload()
    original["features"][1]["id"] = original["features"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        stored_locations(original)


@pytest.mark.parametrize("name", ["", "EPSG:999999999", "unknown-crs"])
def test_unknown_crs(name: str) -> None:
    original = payload()
    original["crs"]["properties"]["name"] = name
    with pytest.raises((ValueError, CRSError), match=r"CRS|projection"):
        stored_locations(original)


@pytest.mark.parametrize("kind", GEOMETRIES)
@pytest.mark.parametrize(
    "coordinate", [float("nan"), float("inf"), -float("inf"), True, "1"]
)
def test_invalid_coordinates_at_every_depth(kind: str, coordinate: object) -> None:
    original = payload()
    feature = next(f for f in original["features"] if f["geometry"]["type"] == kind)
    # Copy via model serialization so shared test coordinates remain untouched.
    feature = Feature.model_validate(feature).model_dump()
    coordinates = feature["geometry"]["coordinates"]
    while isinstance(coordinates[0], list):
        coordinates = coordinates[0]
    coordinates[0] = coordinate
    original["features"] = [feature]
    with pytest.raises(ValidationError, match="coordinates"):
        geometry_buffers.encode(row["geometry"] for row in original["features"])


@pytest.mark.parametrize(
    ("kind", "coordinates"),
    [
        ("Point", [1]),
        ("Point", [[1, 2]]),
        ("LineString", [[1, 2]]),
        ("MultiLineString", [[[1, 2]]]),
        ("Polygon", []),
        ("Polygon", [[[0, 0]] * 3]),
        ("Polygon", [[[0, 0], [1, 0], [1, 1], [0, 1]]]),
        ("Polygon", [[[0], [1], [1], [0]]]),
        ("MultiPolygon", [[]]),
        ("MultiPoint", [[1]]),
        ("GeometryCollection", []),
    ],
)
def test_malformed_geometries(kind: str, coordinates: list) -> None:
    original = payload()
    original["features"][0]["geometry"] = {"type": kind, "coordinates": coordinates}
    with pytest.raises(ValidationError):
        geometry_buffers.encode(row["geometry"] for row in original["features"])


@pytest.mark.parametrize("value", [[], {}, b"bytes", float("inf")])
def test_unsupported_native_properties(value: object) -> None:
    original = payload()
    original["features"] = original["features"][:1]
    original["features"][0]["properties"] = {"reading": value}
    with pytest.raises(ValueError, match=r"Unsupported|infinity"):
        stored_locations(original)


def test_topology_orientation_and_coordinate_ranges_are_plugin_owned() -> None:
    original = payload()
    original["features"][0]["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2000, 2000], [0, 2000], [2000, 0], [0, 0]]],
    }
    value = stored_locations(original)
    assert not value.geometry.iloc[0].is_valid
    assert value.geometry.iloc[0].bounds == (0, 0, 2000, 2000)
