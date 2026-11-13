from typing import Any

import pytest
from pydantic import ValidationError
from shapely.geometry import shape

from lyra.contracts.locations import LocationSelection
from lyra.data import geometry_buffers
from test_support.location_helpers import scoped_payload, stored_locations
from tests.geometry_fixtures import GEOMETRIES, station_collection


@pytest.mark.parametrize("kinds", [[kind] for kind in GEOMETRIES] + [list(GEOMETRIES)])
def test_geometry_round_trip_and_conversion(kinds: list[str]) -> None:
    payload = scoped_payload(station_collection(kinds))
    location = stored_locations(scoped_payload(payload))

    frame = location.copy(deep=True).set_index("id")
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
        "provider": "observatory_stations",
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
        {"ids": [1]},
        {"ids": [True]},
        {"ids": [b"station"]},
        {"ids": [""]},
        {"ids": "station"},
        {"ids": ("station",)},
        {"provider": ""},
        {"provider": 1},
        {"provider": None},
        {"geometry": {"type": "Point", "coordinates": [0, 1]}},
        {"features": []},
        {"crs": "EPSG:4326"},
        {"location": station_collection(["Point"])},
    ],
)
def test_selection_rejects_malformed_fields(changes: dict[str, Any]) -> None:
    payload = {
        "provider": "observatory_stations",
        "ids": ["001"],
    }
    with pytest.raises(ValidationError):
        LocationSelection.model_validate(payload | changes)


@pytest.mark.parametrize("field", ["provider", "ids"])
def test_selection_requires_scope_and_ids(field: str) -> None:
    payload = {
        "provider": "observatory_stations",
        "ids": ["001"],
    }
    del payload[field]
    with pytest.raises(ValidationError):
        LocationSelection.model_validate(payload)


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
    payload = station_collection(["Point"])
    payload["features"][0]["geometry"] = geometry
    with pytest.raises(ValidationError):
        geometry_buffers.encode([geometry])
