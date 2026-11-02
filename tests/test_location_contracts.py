import pandera.pandas as pa
import pytest
from lyra.sdk import LocationContract, LocationFamily
from pydantic import ValidationError

from tests.test_location_schema import column, contract, declaration


def dataset(**changes: object) -> LocationContract:
    family = LocationFamily.from_schema(
        "example:places",
        "point",
        contract(declaration(count=column(int, checks=pa.Check.ge(0)))),
    )
    return LocationContract.model_validate(
        {
            "family": family,
            "source": "survey-2020",
            "geographical_edition": "boundaries-2010",
            "meaning": "Population survey",
            "observation_period": "2020",
            "release": "final",
            "field_definitions": {"count": "Resident population"},
            **changes,
        }
    )


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
