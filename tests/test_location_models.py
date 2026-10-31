import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from lyra.sdk import Locations, LocationSelection
from lyra.sdk.models.location_payload import LocationPayload
from pydantic import ValidationError
from shapely.geometry import shape

from tests.location_helpers import scoped_payload, stored_locations

RING = [[0, 0], [2, 0], [2, 2], [0, 0]]
GEOMETRIES = {
    "Point": [0, 1],
    "MultiPoint": [[0, 1], [2, 3]],
    "LineString": [[0, 1], [2, 3]],
    "MultiLineString": [[[0, 1], [2, 3]], [[4, 5], [6, 7]]],
    "Polygon": [RING],
    "MultiPolygon": [[RING]],
}


def _collection(kinds: list[str]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": f"000{i}:station",
                "type": "Feature",
                "geometry": {"type": kind, "coordinates": GEOMETRIES[kind]},
                "properties": {"label": kind, "reading": i},
            }
            for i, kind in enumerate(kinds)
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:27700"}},
    }


@pytest.mark.parametrize("kinds", [[kind] for kind in GEOMETRIES] + [list(GEOMETRIES)])
def test_geometry_round_trip_and_conversion(kinds: list[str]) -> None:
    payload = scoped_payload(_collection(kinds))
    Draft202012Validator(LocationPayload.model_json_schema()).validate(payload)
    location = stored_locations(scoped_payload(payload))
    assert json.loads(location.to_json()) == location.to_dict()
    assert location.to_dict() == payload

    frame = location.to_geodataframe().set_index("id")
    assert list(frame.index) == [feature["id"] for feature in payload["features"]]
    assert frame.crs is not None
    assert frame.crs.to_epsg() == 27700
    assert frame.geometry.geom_type.tolist() == kinds
    for feature in payload["features"]:
        assert frame.loc[feature["id"], "geometry"].equals_exact(
            shape(feature["geometry"]), tolerance=0
        )
        for key, value in feature["properties"].items():
            assert frame.loc[feature["id"], key] == value


def test_selection_preserves_opaque_ids_and_order() -> None:
    payload = {
        "collection": "observatory:stations",
        "unit_type": "sensor",
        "ids": ["0001", "X", "station/long-id", " X ", "0001"],
    }
    selection = LocationSelection.model_validate(payload)
    assert selection.model_dump() == payload
    assert (
        LocationSelection.model_validate_json(selection.model_dump_json()) == selection
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ids": []},
        {"ids": [1]},
        {"ids": [True]},
        {"ids": [b"station"]},
        {"ids": [""]},
        {"ids": "station"},
        {"ids": ("station",)},
        {"collection": ""},
        {"collection": "stations"},
        {"collection": ":stations"},
        {"collection": "observatory:"},
        {"collection": "observatory:station:extra"},
        {"unit_type": ""},
        {"geometry": {"type": "Point", "coordinates": [0, 1]}},
        {"features": []},
        {"crs": "EPSG:4326"},
        {"location": _collection(["Point"])},
    ],
)
def test_selection_rejects_malformed_fields(changes: dict[str, Any]) -> None:
    payload = {
        "collection": "observatory:stations",
        "unit_type": "sensor",
        "ids": ["001"],
    }
    with pytest.raises(ValidationError):
        LocationSelection.model_validate(payload | changes)


@pytest.mark.parametrize("field", ["collection", "unit_type", "ids"])
def test_selection_requires_scope_and_ids(field: str) -> None:
    payload = {
        "collection": "observatory:stations",
        "unit_type": "sensor",
        "ids": ["001"],
    }
    del payload[field]
    with pytest.raises(ValidationError):
        LocationSelection.model_validate(payload)


def test_resolved_location_round_trip_retains_scope_and_handler_value() -> None:
    payload = {
        "collection": "observatory:stations",
        "unit_type": "sensor",
        **{k: v for k, v in _collection(list(GEOMETRIES)).items() if k != "type"},
    }
    resolved = stored_locations(scoped_payload(payload))
    restored = json.loads(resolved.to_json())
    assert restored == resolved.to_dict()
    assert restored == scoped_payload(payload)
    assert isinstance(resolved, Locations)
    assert restored["collection"] == "observatory:stations"


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "GeometryCollection", "geometries": []},
        {"type": "Point", "coordinates": [[1, 2]]},
        {"type": "LineString", "coordinates": [1, 2]},
        {"type": "MultiLineString", "coordinates": [[1, 2]]},
        {"type": "Point", "coordinates": ["invalid", 2]},
        {"type": "Point", "coordinates": [1]},
        {"type": "MultiPoint", "coordinates": [[1]]},
        {"type": "LineString", "coordinates": [[1, 2]]},
        {"type": "LineString", "coordinates": [[1], [2]]},
        {"type": "MultiLineString", "coordinates": [[[1, 2]]]},
        {"type": "MultiLineString", "coordinates": [[[1], [2]]]},
    ],
)
def test_geometry_rejects_unsupported_types_and_structure(
    geometry: dict[str, Any],
) -> None:
    payload = _collection(["Point"])
    payload["features"][0]["geometry"] = geometry
    with pytest.raises(ValidationError):
        stored_locations(scoped_payload(payload))
    with pytest.raises(SchemaValidationError):
        Draft202012Validator(LocationPayload.model_json_schema()).validate(payload)
