"""Native buffer validation and detached exports at the ownership boundary."""

from unittest.mock import Mock

import pyarrow as pa
import pytest
from lyra.sdk import geometry_buffers, property_buffers
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.geometry import Feature
from lyra.sdk.models.location import locations_from_snapshot

from benchmarks.location_baseline.fixtures import assert_exact, payload
from tests.test_location_snapshot import columns


@pytest.mark.parametrize("rows", [0, 12, 4101])
def test_validation_does_not_materialize(
    rows: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    geometry, properties = columns(payload(rows))
    forbidden = Mock(side_effect=AssertionError("materialization during validation"))
    monkeypatch.setattr(geometry_buffers, "decode", forbidden)
    monkeypatch.setattr(geometry_buffers, "geometry_rows", forbidden)
    monkeypatch.setattr(property_buffers, "decode", forbidden)
    monkeypatch.setattr(property_buffers, "property_data", forbidden)
    monkeypatch.setattr(property_buffers, "encode", forbidden)
    snapshot = LocationSnapshot(geometry, properties)
    assert snapshot.geometry.num_rows == rows


def test_exports_do_not_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = payload()
    snapshot = LocationSnapshot(*columns(raw))
    value = locations_from_snapshot(snapshot)
    forbidden = Mock(side_effect=AssertionError("revalidation during export"))
    monkeypatch.setattr(geometry_buffers, "validate", forbidden)
    monkeypatch.setattr(property_buffers, "validate", forbidden)
    monkeypatch.setattr(Feature, "model_validate", forbidden)
    monkeypatch.setattr(geometry_buffers, "_ADAPTER", forbidden)
    assert_exact(snapshot.to_dict(), raw)
    first, second = value.features, value.features
    assert_exact([f.model_dump() for f in first], raw["features"])
    first[0].geometry.coordinates.clear()
    first[0].properties["changed"] = True
    first.clear()
    assert_exact([f.model_dump() for f in second], raw["features"])
    assert_exact(value.model_dump(), raw)
    assert value.model_dump(include={"features": {0: {"id"}}}) == {
        "features": [{"id": "0"}]
    }


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_bad_export_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        LocationSnapshot(*columns(payload())).to_dict(limit=limit)


@pytest.mark.parametrize("tag", range(6))
@pytest.mark.parametrize("level", range(5))
def test_nulls_at_every_geometry_depth(tag: int, level: int) -> None:
    nested = None
    for _ in range(level):
        nested = [nested]
    table = pa.Table.from_pylist(
        [{"type": tag, "coordinates": nested}], schema=geometry_buffers.SCHEMA
    )
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        geometry_buffers.validate(table)


@pytest.mark.parametrize(
    "kind",
    ["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"],
)
@pytest.mark.parametrize("width", [1, 2, 3, 5])
def test_native_geometry_matches_model_oracle(kind: str, width: int) -> None:
    point = [float(i) for i in range(width)]
    line = [point, [v + 1 for v in point]]
    ring = [point, *line, point]
    coordinates = {
        "Point": point,
        "MultiPoint": line,
        "LineString": line,
        "MultiLineString": [line],
        "Polygon": [ring],
        "MultiPolygon": [[ring]],
    }[kind]
    raw = {"type": kind, "coordinates": coordinates}
    depths = {
        "Point": 3,
        "MultiPoint": 2,
        "LineString": 2,
        "MultiLineString": 1,
        "Polygon": 1,
        "MultiPolygon": 0,
    }
    nested = coordinates
    for _ in range(depths[kind]):
        nested = [nested]
    tag = list(depths).index(kind)
    table = pa.Table.from_pylist(
        [{"type": tag, "coordinates": nested}], schema=geometry_buffers.SCHEMA
    )
    feature = {"type": "Feature", "id": "a", "geometry": raw, "properties": {}}
    if width == 1:
        with pytest.raises(ValueError, match=r"Invalid|validation error"):
            Feature.model_validate(feature)
        with pytest.raises(ValueError, match=r"Invalid|validation error"):
            geometry_buffers.validate(table)
    else:
        expected = Feature.model_validate(feature).geometry.model_dump()
        geometry_buffers.validate(table)
        assert_exact(geometry_buffers.decode(table)[0].model_dump(), expected)


@pytest.mark.parametrize(
    "damage", ["null", "tag", "inactive", "integer", "float", "active"]
)
def test_invalid_property_payloads(damage: str) -> None:
    _, table = columns(payload())
    cells = table.column(1).to_pylist()
    if damage == "null":
        cells[0] = None
    elif damage == "tag":
        cells[0]["tag"] = None
    elif damage == "inactive":
        cells[0]["float"] = 1.0
    elif damage == "integer":
        cells[0].update(tag=4, integer="0x01")
    elif damage == "float":
        cells[0].update(tag=5, float=float("inf"))
    else:
        cells[0]["tag"] = 2
    invalid = table.set_column(
        1, table.schema.field(1), pa.array(cells, type=table.column(1).type)
    )
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        property_buffers.validate(invalid)


def test_slices_ignore_unreferenced_invalid_values() -> None:
    geometry, properties = columns(payload())
    tags = geometry.column(0).to_pylist()
    tags[0] = 99
    geometry = geometry.set_column(
        0, geometry.schema.field(0), pa.array(tags, type=pa.int8())
    )
    geometry_buffers.validate(geometry.slice(1))
    chunks = pa.concat_tables(
        [properties.slice(0, 2), properties.slice(2), properties.slice(0, 1)]
    )
    with pytest.raises(ValueError, match="unique"):
        property_buffers.validate(chunks)


@pytest.mark.parametrize("last", [[9.0, 9.0], [0.0, 0.0, 1.0], [-0.0, 0.0]])
def test_ring_endpoints_are_compared_without_changing_bits(last: list[float]) -> None:
    ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], last]
    table = pa.Table.from_pylist(
        [{"type": 4, "coordinates": [[ring]]}], schema=geometry_buffers.SCHEMA
    )
    if last == [-0.0, 0.0]:
        geometry_buffers.validate(table)
        assert_exact(geometry_buffers.decode(table)[0].coordinates, [ring])
    else:
        with pytest.raises(ValueError, match="closed"):
            geometry_buffers.validate(table)


def test_property_decode_does_not_reencode(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = payload()
    _, table = columns(raw)
    monkeypatch.setattr(
        property_buffers, "encode", Mock(side_effect=AssertionError("reencoding"))
    )
    result = property_buffers.decode(table)
    assert_exact(result["properties"], [f["properties"] for f in raw["features"]])
