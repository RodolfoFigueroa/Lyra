"""Native buffer validation and detached exports at the ownership boundary."""

from unittest.mock import Mock

import pyarrow as pa
import pytest

from lyra.contracts.geometry import Feature
from lyra.data import geometry_buffers
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.locations import locations_from_snapshot
from test_support.location_data import assert_exact, columns, payload


@pytest.mark.parametrize("rows", [0, 12, 4101])
def test_validation_does_not_materialize(
    rows: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties, geometry, scope = columns(payload(rows))
    forbidden = Mock(side_effect=AssertionError("materialization during validation"))
    monkeypatch.setattr(geometry_buffers, "decode", forbidden)
    monkeypatch.setattr(geometry_buffers, "geometry_rows", forbidden)
    snapshot = LocationSnapshot(properties, geometry, scope)
    assert snapshot.geometry.num_rows == rows


def test_exports_do_not_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = payload()
    snapshot = LocationSnapshot(*columns(raw))
    value = locations_from_snapshot(snapshot)
    forbidden = Mock(side_effect=AssertionError("revalidation during export"))
    monkeypatch.setattr(geometry_buffers, "validate", forbidden)
    monkeypatch.setattr(Feature, "model_validate", forbidden)
    assert_exact(snapshot.to_dict(), raw)
    first, second = value.to_features(), value.to_features()
    assert_exact([f.model_dump() for f in first], raw["features"])
    first[0].geometry.coordinates.clear()
    first[0].properties["changed"] = True
    first.clear()
    assert_exact([f.model_dump() for f in second], raw["features"])
    assert_exact(value.to_dict(), raw)
    assert value.to_dict(limit=1)["features"][0]["id"] == "0"


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_bad_export_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        LocationSnapshot(*columns(payload())).to_dict(limit=limit)


def test_slices_ignore_unreferenced_invalid_values() -> None:
    properties, geometry, scope = columns(payload())
    tags = geometry.column(0).to_pylist()
    tags[0] = b"invalid"
    geometry = geometry.set_column(
        0, geometry.schema.field(0), pa.array(tags, type=pa.binary())
    )
    geometry_buffers.validate(geometry.slice(1))
    geometry = columns(payload())[1]
    chunks = pa.concat_tables(
        [properties.slice(0, 2), properties.slice(2), properties.slice(0, 1)]
    )
    with pytest.raises(ValueError, match="unique"):
        LocationSnapshot(
            chunks,
            pa.concat_tables(
                [geometry.slice(0, 2), geometry.slice(2), geometry.slice(0, 1)]
            ),
            scope,
        )


def test_property_decode_does_not_reencode(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = payload()
    snapshot = LocationSnapshot(*columns(raw))
    monkeypatch.setattr(
        LocationSnapshot, "__init__", Mock(side_effect=AssertionError("reencoding"))
    )
    assert_exact(
        snapshot.property_data()["properties"],
        [f["properties"] for f in raw["features"]],
    )
