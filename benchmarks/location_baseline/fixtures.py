"""Reusable raw inputs and representation-sensitive comparisons."""

import copy
import struct

import pandas as pd
import pyarrow as pa
from lyra.sdk import Locations, geometry_buffers
from lyra.sdk.typed_storage import Scope


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
        ("Point", [1.0, 2.0, 3.0, 4.0]),
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
    """Return invalid external boundary inputs for reuse by later phases."""
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
    if isinstance(value, Locations):
        return exact(value.to_dict())
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


def columns(raw: dict) -> tuple:
    """Build native test buffers.

    Returns:
        Typed columns, aligned geometry, and scope.

    Raises:
        ValueError: If properties are incomplete.
    """
    features = raw["features"]
    keys = list(features[0]["properties"]) if features else []
    if any(set(f["properties"]) != set(keys) for f in features):
        message = "missing or unexpected properties"
        raise ValueError(message)
    arrays = [pa.array([f["id"] for f in features], type=pa.string())]
    for key in keys:
        values = [f["properties"][key] for f in features]
        arrays.append(
            pa.array(
                values, type=pa.int64() if all(v is None for v in values) else None
            )
        )
    table = pa.Table.from_arrays(arrays, names=["id", *keys])
    # Native metadata retains nullable integers and booleans on frame export.
    data = {"id": pd.Series([f["id"] for f in features], dtype=object)}
    for key, array in zip(keys, arrays[1:], strict=True):
        dtype = (
            "Int64"
            if pa.types.is_int64(array.type) and array.null_count
            else "boolean"
            if pa.types.is_boolean(array.type) and array.null_count
            else None
        )
        data[key] = pd.Series(array.to_pylist(), dtype=dtype or array.to_pandas().dtype)
    table = pa.Table.from_pandas(pd.DataFrame(data), preserve_index=False)
    if not features:
        table = table.set_column(
            0, pa.field("id", pa.string()), pa.array([], type=pa.string())
        )
    scope = Scope(
        raw["collection"],
        raw["unit_type"],
        raw["crs"]["properties"]["name"],
        "id",
        "geometry",
        ("id", "geometry", *keys),
    )
    return table, geometry_buffers.encode(f["geometry"] for f in features), scope
