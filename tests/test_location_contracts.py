import pandera.pandas as pa
import pytest
from pydantic import ValidationError

from lyra.contracts.locations import LocationContract, LocationFamily
from test_support.location_schema import column, contract, dataset, declaration


def test_identity_and_revision_scopes() -> None:
    first = dataset()
    corrected = dataset(data_revision="r1")
    other = dataset(source="survey-2021", observation_period="2021", data_revision="r1")
    geography = dataset(geographical_edition="boundaries-2020")
    assert first.reference == corrected.reference
    assert first.edition is corrected.edition is None
    assert (
        first.unit_identity("1")
        == corrected.unit_identity("1")
        == other.unit_identity("1")
    )
    assert first.unit_identity("1") != geography.unit_identity("1")
    assert corrected.reference != other.reference
    assert len({first.reference, other.reference, geography.reference}) == 3
    assert first.data_revision is None


@pytest.mark.parametrize("changes", [{}, {"edition": "2020", "data_revision": "r1"}])
def test_round_trip(changes: dict[str, str]) -> None:
    value = dataset(**changes)
    assert LocationContract.model_validate_json(value.model_dump_json()) == value
    reference = value.reference
    assert type(reference).model_validate_json(reference.model_dump_json()) == reference
    identity = value.unit_identity("001")
    assert type(identity).model_validate_json(identity.model_dump_json()) == identity


def test_shared_structure_and_definitions() -> None:
    first = dataset()
    second = dataset(family=first.family, source="other")
    assert second.family is first.family
    count = first.family.structure.output.columns[-1]
    assert count.checks[0].parameters == {"min_value": 0}
    assert not count.nullable
    with pytest.raises(ValidationError, match="declared columns"):
        dataset(field_definitions={"missing": "Unknown"})
    payload = first.family.model_dump()
    payload["structure"]["id_column"] = "shape"
    with pytest.raises(ValidationError, match="distinct"):
        LocationFamily.model_validate(payload)


def test_schema_family_owns_detached_metadata() -> None:
    schema = contract(declaration(count=column(int, checks=pa.Check.ge(0))))
    family = schema.family("example:places", "point")
    original = family.model_dump_json()
    assert family.structure == schema.metadata
    assert family.structure is not schema.metadata
    schema.metadata.output.columns[-1].checks[0].parameters["min_value"] = 10
    assert family.model_dump_json() == original
    family.structure.output.columns[-1].description = "Independent description"
    assert schema.metadata.output.columns[-1].description != "Independent description"
    with pytest.raises(ValidationError):
        schema.family("not-namespaced", "point")
