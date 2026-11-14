import pytest

from lyra.data.geometry_buffers import decode, encode, geometry_model
from test_support.location_data import assert_exact, payload
from tests.geometry_fixtures import VALUE_GEOMETRIES as GEOMETRIES


@pytest.mark.parametrize(
    "coordinates",
    [[[0, 0], [1, 1, 1]], [[0, 0, 0, 0], [1, 1, 1, 1]]],
)
def test_unrepresentable_dimensions(coordinates: list) -> None:
    raw = payload(1)
    raw["features"][0]["geometry"] = {"type": "LineString", "coordinates": coordinates}
    with pytest.raises(ValueError, match=r"dimension|inhomogeneous|validation error"):
        decode(encode(f["geometry"] for f in raw["features"]))


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
    restored = decode(encode(f["geometry"] for f in raw["features"]))
    assert_exact(
        restored[0].model_dump(),
        geometry_model(raw["features"][0]["geometry"]).model_dump(),
    )


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
    restored = decode(encode(f["geometry"] for f in raw["features"]))
    assert_exact(
        restored[0].model_dump(),
        geometry_model(raw["features"][0]["geometry"]).model_dump(),
    )
