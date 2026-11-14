"""Shared production regression fixtures and assertions."""

import copy
import struct


def payload(rows: int = 12) -> dict:
    """Return independent features covering supported geometry nesting.

    Returns:
        A raw collection with scalar properties and ordered IDs.
    """
    point = [1.0, 2.0]
    line = [point, [3.0, 4.0]]
    ring = [point, [3.0, 2.0], [3.0, 4.0], point]
    geometries = [
        ("Point", point),
        ("Point", [1.0, 2.0, 3.0]),
        ("LineString", line),
        ("Polygon", [ring, ring]),
        ("MultiPoint", line),
        ("MultiLineString", [line, line]),
        ("MultiPolygon", [[ring], [ring, ring]]),
        ("MultiPoint", []),
        ("MultiLineString", []),
        ("MultiPolygon", []),
        ("Point", [4.0, 5.0, 6.0]),
        ("Point", [-0.0, 2.0]),
    ]
    scalars = [0, 1, 2, 2**60 + 1, 4, None]
    return copy.deepcopy(
        {
            "collection": "benchmark:places",
            "unit_type": "unit",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(i),
                    "geometry": {
                        "type": geometries[i % 12][0],
                        "coordinates": geometries[i % 12][1],
                    },
                    "properties": {"value": scalars[i % 6]},
                }
                for i in range(rows)
            ],
        }
    )


def variants() -> dict[str, dict]:
    """Return valid scope, CRS, presence, order, and same-ID content variants."""
    base = payload()
    result = {"base": base, "empty": payload(0)}
    for name in ("scope", "crs", "null", "reordered", "changed", "feature_order"):
        value = copy.deepcopy(base)
        if name == "scope":
            value["unit_type"] = "other"
        elif name == "crs":
            value["crs"]["properties"]["name"] = "EPSG:3857"
        elif name == "null":
            value["features"][0]["properties"]["value"] = None
        elif name == "reordered":
            value = dict(reversed(list(value.items())))
        elif name == "feature_order":
            value["features"].reverse()
        else:
            value["features"][0]["geometry"]["coordinates"][0] = 9.0
        result[name] = value
    return result


def invalid_inputs() -> dict[str, dict]:
    """Return reusable invalid inputs for external boundary validation."""
    result = {}
    for name in ("duplicate", "nonfinite", "nested_property", "open_ring", "crs"):
        value = payload()
        if name == "duplicate":
            value["features"][1]["id"] = "0"
        elif name == "nonfinite":
            value["features"][0]["geometry"]["coordinates"][0] = float("inf")
        elif name == "nested_property":
            value["features"][0]["properties"] = {"bad": []}
        elif name == "open_ring":
            value["features"][3]["geometry"]["coordinates"][0][-1] = [9.0, 9.0]
        else:
            value["crs"]["properties"]["name"] = "invalid"
        result[name] = value
    return result


def exact(value: object) -> object:
    """Return a comparable token preserving scalar types and ordered structure.

    Returns:
        A recursive token ignoring only dictionary insertion order.
    """
    if isinstance(value, dict):
        return ("dict", tuple(sorted((k, exact(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(exact(v) for v in value))
    if isinstance(value, float):
        return ("float", struct.pack("!d", value))
    return (type(value).__name__, value)


def assert_exact(actual: object, expected: object) -> None:
    """Reject any content loss.

    Raises:
        AssertionError: When exact tokens differ.
    """
    if exact(actual) != exact(expected):
        msg = "Location content differs"
        raise AssertionError(msg)
