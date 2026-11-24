import pytest
from pydantic import ValidationError

from lyra.contracts.locations import LocationContract
from test_support.location_schema import dataset


def test_identity_and_revision_scopes() -> None:
    first = dataset()
    other = dataset(provider_scope={"provider_id": "other"})
    revised = dataset(
        provider_scope={"provider_id": "example:places", "code_revision": "2"}
    )
    assert (
        len(
            {
                first.unit_identity("1"),
                other.unit_identity("1"),
                revised.unit_identity("1"),
            }
        )
        == 3
    )
    for value in (first, other, revised):
        assert LocationContract.model_validate_json(value.model_dump_json()) == value
        assert (
            type(value.reference).model_validate_json(value.reference.model_dump_json())
            == value.reference
        )


def test_shared_structure_and_definitions() -> None:
    first = dataset()
    count = first.structure.output.columns[-1]
    assert count.checks[0].parameters == {"min_value": 0}
    assert not count.nullable
    with pytest.raises(ValidationError, match="declared columns"):
        dataset(field_definitions={"missing": "Unknown"})
    payload = first.model_dump()
    payload["structure"]["id_column"] = "shape"
    with pytest.raises(ValidationError, match="distinct"):
        LocationContract.model_validate(payload)
