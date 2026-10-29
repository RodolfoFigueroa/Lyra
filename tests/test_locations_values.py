from datetime import UTC, date, datetime
from typing import Any

import pytest
from lyra.sdk import Locations, LocationSelection
from lyra.sdk.models.geometry import CRS, Feature
from pydantic import ValidationError

RING = [[0, 0], [2, 0], [2, 2], [0, 0]]
GEOMETRIES = {
    "Point": [0, 1, 2],
    "MultiPoint": [[0, 1], [2, 3]],
    "LineString": [[0, 1], [2, 3]],
    "MultiLineString": [[[0, 1], [2, 3]]],
    "Polygon": [RING],
    "MultiPolygon": [[RING]],
}


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
    value = Locations.model_validate(original)
    assert value.model_dump(mode="json") == original
    assert Locations.model_validate_json(value.model_dump_json()) == value
    assert value.ids == [feature["id"] for feature in original["features"]]
    value.ids.reverse()
    assert value.ids[0] == "000"


def test_empty_values_retain_scope_and_crs_without_allowing_empty_selections() -> None:
    original = payload() | {"features": []}
    value = Locations.model_validate(original)
    assert value.ids == []
    assert (
        Locations.model_validate_json(value.model_dump_json()).model_dump() == original
    )
    with pytest.raises(ValidationError):
        LocationSelection(
            collection=value.collection, unit_type=value.unit_type, ids=[]
        )


@pytest.mark.parametrize("identifier", [1, True, b"id", "", None])
def test_invalid_ids(identifier: object) -> None:
    original = payload()
    original["features"][0]["id"] = identifier
    with pytest.raises(ValidationError, match="id"):
        Locations.model_validate(original)


def test_duplicate_ids() -> None:
    original = payload()
    original["features"][1]["id"] = original["features"][0]["id"]
    with pytest.raises(ValidationError, match="unique"):
        Locations.model_validate(original)


@pytest.mark.parametrize("name", ["", "EPSG:999999999", "unknown-crs"])
def test_unknown_crs(name: str) -> None:
    original = payload()
    original["crs"]["properties"]["name"] = name
    with pytest.raises(ValidationError, match=r"CRS|name"):
        Locations.model_validate(original)


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
        Locations.model_validate(original)


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
        Locations.model_validate(original)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        b"bytes",
        date(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=UTC),
        float("nan"),
        float("inf"),
    ],
)
def test_unsupported_properties_identify_column(value: object) -> None:
    original = payload()
    original["features"][0]["properties"] = {"reading": value}
    with pytest.raises(ValidationError, match=r"reading.*finite JSON scalar"):
        Locations.model_validate(original)


def test_existing_feature_objects_are_revalidated() -> None:
    original = payload()
    feature = Feature.model_validate(original["features"][0])
    feature.id = ""
    original["features"] = [feature]
    with pytest.raises(ValidationError, match="id"):
        Locations.model_validate(original)


def test_existing_crs_objects_are_revalidated() -> None:
    original = payload()
    crs = CRS.model_validate(original["crs"])
    original["crs"] = crs.model_copy(update={"type": "invalid"})
    with pytest.raises(ValidationError, match=r"crs.type"):
        Locations.model_validate(original)


def test_topology_orientation_and_coordinate_ranges_are_plugin_owned() -> None:
    original = payload()
    original["features"][0]["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2000, 2000], [0, 2000], [2000, 0], [0, 0]]],
    }
    value = Locations.model_validate(original)
    assert value.model_dump(mode="json") == original
