"""Shared-invariant trust is tied to owned instances, never copied markers."""

from unittest.mock import patch

import pytest
from lyra.sdk.models.location import Locations, validated_locations
from pydantic import ValidationError

from tests.test_location_schema import column, contract, declaration, locations


def test_forwarding_reuses_owned_value_without_public_serialization() -> None:
    value = locations({"count": 1})
    with (
        patch.object(Locations, "model_validate", side_effect=AssertionError),
        patch.object(Locations, "model_dump", side_effect=AssertionError),
    ):
        assert validated_locations(value) is value
        assert validated_locations(value) is value


def test_copied_snapshot_does_not_establish_trust() -> None:
    value = locations({})
    copy = value.model_copy(update={"collection": "invalid"})
    with pytest.raises(ValidationError):
        validated_locations(copy)


def test_shadowed_fields_are_revalidated() -> None:
    value = locations({})
    value.__dict__["features"] = ["invalid"]
    with pytest.raises(ValidationError):
        validated_locations(value)


def test_receiving_schema_is_always_checked() -> None:
    value = locations({"count": 1})
    first = contract(declaration(count=column(int)))
    validated = first.validate(value)
    second = contract(declaration(other=column(int)))
    with pytest.raises(ValueError, match="missing properties"):
        second.validate(validated)


@pytest.mark.parametrize("fields", [{}, {"features": None}])
def test_unchecked_incomplete_models_raise_validation_errors(fields: dict) -> None:
    value = Locations.model_construct(**fields)
    with pytest.raises(ValidationError):
        validated_locations(value)


def test_unchecked_noniterable_features_raise_validation_errors() -> None:
    valid = locations({})
    value = Locations.model_construct(
        collection=valid.collection,
        unit_type=valid.unit_type,
        crs=valid.crs,
        features=None,
    )
    with pytest.raises(ValidationError):
        validated_locations(value)
