"""Native snapshot wrapping enforces integrity independently of producer checks."""

from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest

from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.location_validation import validate_snapshot
from test_support.location_data import columns, payload

_set_frozen = object.__setattr__


@pytest.mark.parametrize("collection", ["", None, 1, b"records"])
def test_snapshot_rejects_invalid_collection_identifiers(collection: object) -> None:
    table, geometry, scope = columns(payload(1))
    _set_frozen(scope, "collection", collection)
    with pytest.raises(ValueError, match="Invalid location structural metadata"):
        LocationSnapshot(table, geometry, scope)


@pytest.mark.parametrize(
    "updates",
    [
        {"collection": ""},
        {"unit_type": "two words"},
        {"crs": "not-a-crs"},
        {"metric": True},
        {"id_column": "absent"},
        {"columns": ("id", "geometry")},
    ],
)
def test_snapshot_checks_scope(updates: dict) -> None:
    table, geometry, scope = columns(payload(1))
    snapshot = LocationSnapshot(table, geometry, scope)
    invalid_scope = replace(scope)
    for name, value in updates.items():
        _set_frozen(invalid_scope, name, value)
    _set_frozen(snapshot, "scope", invalid_scope)
    with pytest.raises(
        ValueError,
        match=r"scope|metadata|identifier|validation error|property|infinity|CRS",
    ):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("ids", [[1, 2], ["", "b"], ["a", "a"], [None, "b"]])
def test_snapshot_checks_identifiers(ids: list) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    _set_frozen(snapshot, "table", pa.table({"id": ids, "value": [1, 2]}))
    with pytest.raises(
        ValueError,
        match=r"scope|metadata|identifier|validation error|property|infinity|CRS",
    ):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("values", [[[1], [2]], [float("inf"), 1.0], [b"a", b"b"]])
def test_snapshot_checks_scalar_properties(values: list) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    _set_frozen(snapshot, "table", pa.table({"id": ["a", "b"], "value": values}))
    with pytest.raises(
        ValueError,
        match=r"scope|metadata|identifier|validation error|property|infinity|CRS",
    ):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("readonly", [False, True])
def test_snapshot_owns_unchecked_buffers_and_rejects_misalignment(
    *, readonly: bool
) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    values = np.array([1, 2], dtype="int64")
    external = values.view()
    external.flags.writeable = not readonly
    _set_frozen(snapshot, "table", pa.table({"id": ["a", "b"], "value": external}))
    value = validate_snapshot(snapshot)
    assert type(value) is LocationSnapshot
    values[0] = 99
    assert value.table["value"].to_pylist() == [1, 2]
    buffer = value.table["value"].chunk(0).buffers()[1]
    assert buffer is not None
    assert not buffer.is_mutable
    _set_frozen(snapshot, "geometry", geometry.slice(0, 1))
    with pytest.raises(ValueError, match="row counts"):
        validate_snapshot(snapshot)


def test_owned_views_reuse_validated_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = LocationSnapshot(*columns(payload(2)))
    monkeypatch.setattr(
        "shapely.from_wkb",
        lambda *_args, **_kwargs: pytest.fail("Owned view decoded geometry again"),
    )
    value = validate_snapshot(snapshot)
    assert value.slice(1).table["id"].to_pylist() == ["1"]
    assert value.take(pa.array([1, 0])).table["id"].to_pylist() == ["1", "0"]
    assert value.to_frame()["id"].tolist() == ["0", "1"]
