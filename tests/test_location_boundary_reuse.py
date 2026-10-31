"""Owned runtime boundaries reject incomplete values and external payloads."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    MetricResultError,
    content_fingerprints,
    geometry_shapely,
    location_artifacts,
)
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import (
    Locations,
    location_snapshot,
    locations_from_snapshot,
    validated_locations,
)

from tests.test_location_schema import column, contract, declaration, locations


def test_forwarding_reuses_owned_value_without_public_serialization() -> None:
    value = locations({"count": 1})
    with patch.object(Locations, "to_dict", side_effect=AssertionError):
        assert validated_locations(value) is value


def test_runtime_is_frozen_and_slotted() -> None:
    value = locations({})
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(value, "_" + "snapshot", None)


@pytest.mark.parametrize("value", [object.__new__(Locations), {}, None])
def test_incomplete_values_rejected(value: object) -> None:
    with pytest.raises(TypeError):
        validated_locations(value)


def test_receiving_schema_is_always_checked() -> None:
    validated = contract(declaration(count=column(int))).validate(
        locations({"count": 1})
    )
    with pytest.raises(ValueError, match="properties"):
        contract(declaration(other=column(int))).validate(validated)


def test_unchanged_validation_reuses_snapshot_and_cached_identity() -> None:

    schema = contract(declaration(count=column(int)))
    value = schema.validate(locations({"count": 1}))
    snapshot = location_snapshot(value)
    identity = snapshot.fingerprint
    with (
        patch.object(geometry_shapely, "ingest_geometries", side_effect=AssertionError),
        patch.object(LocationSnapshot, "from_locations", side_effect=AssertionError),
        patch.object(
            content_fingerprints, "typed_snapshot", side_effect=AssertionError
        ),
    ):
        assert schema.validate(value) is value
        assert snapshot.fingerprint == identity
        assert snapshot.fingerprint == identity


@pytest.mark.parametrize("restored", [False, True])
def test_current_checks_execute_on_every_validation(*, restored: bool) -> None:

    value = contract(declaration(count=column(int))).validate(locations({"count": 1}))
    if restored:
        value = locations_from_snapshot(
            location_artifacts.decode(
                location_artifacts.encode(location_snapshot(value))
            )
        )
    calls = []

    def check(series: pd.Series) -> bool:
        calls.append(len(series))
        return len(calls) == 1

    current = contract(declaration(count=column(int, checks=pa.Check(check))))
    assert current.validate(value) is value
    with pytest.raises(MetricResultError):
        current.validate(value)
    assert calls == [1, 1]


def test_coercion_reuses_geometry_and_changes_identity() -> None:

    value = locations({"count": "2"})
    source = location_snapshot(value)
    identity = source.fingerprint
    result = contract(declaration(count=column(int, coerce=True))).validate(value)
    changed = location_snapshot(result)
    assert changed.geometry is source.geometry
    assert changed.fingerprint != identity
    assert source.fingerprint == identity
    assert list(value.property("count")) == ["2"]
    assert list(result.property("count")) == [2]
