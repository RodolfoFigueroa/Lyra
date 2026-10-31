"""Owned runtime boundaries reject incomplete values and external payloads."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest
from lyra.sdk.models.location import Locations, validated_locations

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
    with pytest.raises(ValueError, match="missing properties"):
        contract(declaration(other=column(int))).validate(validated)
