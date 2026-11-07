"""Native snapshot wrapping enforces integrity independently of producer checks."""

from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest

from lyra.data import Locations
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.locations import location_snapshot, locations_from_snapshot
from test_support.location_data import columns, payload

_set_frozen = object.__setattr__


@pytest.mark.parametrize(
    "updates",
    [
        {"collection": "unqualified"},
        {"unit_type": "two words"},
        {"crs": "not-a-crs"},
        {"metric": True},
        {"id_column": "absent"},
        {"columns": ("id", "geometry")},
    ],
)
def test_wrapper_checks_scope(updates: dict) -> None:
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
        locations_from_snapshot(snapshot)


@pytest.mark.parametrize("ids", [[1, 2], ["", "b"], ["a", "a"], [None, "b"]])
def test_wrapper_checks_identifiers(ids: list) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    _set_frozen(snapshot, "table", pa.table({"id": ids, "value": [1, 2]}))
    with pytest.raises(
        ValueError,
        match=r"scope|metadata|identifier|validation error|property|infinity|CRS",
    ):
        locations_from_snapshot(snapshot)


@pytest.mark.parametrize("values", [[[1], [2]], [float("inf"), 1.0], [b"a", b"b"]])
def test_wrapper_checks_scalar_properties(values: list) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    _set_frozen(snapshot, "table", pa.table({"id": ["a", "b"], "value": values}))
    with pytest.raises(
        ValueError,
        match=r"scope|metadata|identifier|validation error|property|infinity|CRS",
    ):
        locations_from_snapshot(snapshot)


@pytest.mark.parametrize("readonly", [False, True])
def test_wrapper_owns_unchecked_buffers_and_rejects_misalignment(
    *, readonly: bool
) -> None:
    table, geometry, scope = columns(payload(2))
    snapshot = LocationSnapshot(table, geometry, scope)
    values = np.array([1, 2], dtype="int64")
    external = values.view()
    external.flags.writeable = not readonly
    _set_frozen(snapshot, "table", pa.table({"id": ["a", "b"], "value": external}))
    value = locations_from_snapshot(snapshot)
    assert type(value) is Locations
    values[0] = 99
    assert list(value.property("value")) == [1, 2]
    buffer = location_snapshot(value).table["value"].chunk(0).buffers()[1]
    assert buffer is not None
    assert not buffer.is_mutable
    _set_frozen(snapshot, "geometry", geometry.slice(0, 1))
    with pytest.raises(ValueError, match="row counts"):
        locations_from_snapshot(snapshot)


def test_owned_views_reuse_validated_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = LocationSnapshot(*columns(payload(2)))
    monkeypatch.setattr(
        "shapely.from_wkb",
        lambda *_args, **_kwargs: pytest.fail("Owned view decoded geometry again"),
    )
    value = locations_from_snapshot(snapshot)
    assert list(value.slice(1).ids) == ["1"]
    assert list(value.select_ids(["1", "0"]).ids) == ["1", "0"]
    assert value.to_dataframe()["id"].tolist() == ["0", "1"]
