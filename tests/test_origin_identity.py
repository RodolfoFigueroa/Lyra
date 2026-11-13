import pytest
from pydantic import ValidationError

from lyra.contracts.provider_scope import ProviderScope, RecordIdentity
from lyra.sdk import Origin


@pytest.mark.parametrize("identifier", ["", 123, True, b"census", None])
def test_origin_rejects_invalid_public_ids(identifier: object) -> None:
    with pytest.raises(ValidationError):
        Origin.model_validate({"id": identifier})


@pytest.mark.parametrize("field", ["revision", "description"])
@pytest.mark.parametrize("value", [12, True, b"text", []])
def test_origin_optional_text_is_strict(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Origin.model_validate({"id": "census", field: value})


def test_origin_identity_preserves_opaque_strings_and_metadata() -> None:
    origin = Origin(id=" Census / 001 ", revision="release / arbitrary text")
    described = Origin(
        id=origin.id, revision=origin.revision, description="Public census records"
    )
    assert origin is not described
    assert origin == described
    assert hash(origin) == hash(described)
    assert origin.identity == (" Census / 001 ", "release / arbitrary text")
    assert described.model_dump()["description"] == "Public census records"
    assert Origin(id="census").identity == ("census", None)
    assert Origin(id="census", revision="") != Origin(id="census")
    assert origin != origin.identity


@pytest.mark.parametrize("field", ["id", "revision", "description"])
def test_origin_is_immutable(field: str) -> None:
    origin = Origin(id="census")
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(origin, field, "changed")


def test_origin_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Origin.model_validate({"id": "census", "connection": "database"})


def test_scope_normalizes_origins_and_retains_metadata() -> None:
    census = Origin(id="census", revision="2025", description="Census")
    elevation = Origin(id="elevation")
    scope = ProviderScope.model_validate(
        {"provider_id": "tracts", "origins": [census, elevation, census]}
    )
    equivalent = ProviderScope.model_validate(
        {
            "provider_id": "tracts",
            "origins": [
                elevation,
                Origin(id="census", revision="2025", description="Other text"),
                census,
            ],
        }
    )
    assert scope == equivalent
    assert hash(scope) == hash(equivalent)
    assert len(scope.origins) == 2
    assert census in scope.origins
    assert {item.description for item in scope.origins} == {"Census", None}
    assert scope.code_revision is None
    assert ProviderScope(provider_id="tracts").origins == frozenset()


def test_scope_copies_caller_owned_containers() -> None:
    declaration = {"id": "census", "revision": "2025"}
    origins = [declaration]
    values = {"provider_id": "tracts", "origins": origins}
    scope = ProviderScope.model_validate(values)
    record = RecordIdentity(scope=scope, location_id="001")
    original_hash = hash(record)
    declaration["revision"] = "2026"
    origins.clear()
    values["provider_id"] = "counties"
    assert scope == ProviderScope(
        provider_id="tracts", origins=frozenset({Origin(id="census", revision="2025")})
    )
    assert hash(record) == original_hash
    for target in (scope, record):
        for field in type(target).model_fields:
            with pytest.raises(ValidationError, match="frozen_instance"):
                setattr(target, field, "changed")


def test_shared_origins_and_provider_revisions_determine_record_identity() -> None:
    census = Origin(id="census", revision="2025")
    tracts = ProviderScope(provider_id="tracts", origins=frozenset({census}))
    counties = ProviderScope(provider_id="counties", origins=frozenset({census}))
    tract = RecordIdentity(scope=tracts, location_id="001")
    assert tract != RecordIdentity(scope=counties, location_id="001")
    assert tract != RecordIdentity(scope=tracts, location_id="002")
    assert tract == RecordIdentity(scope=tracts, location_id="001")
    for scope in (tracts, counties):
        revised_data = ProviderScope(
            provider_id=scope.provider_id,
            origins=frozenset({Origin(id="census", revision="2026")}),
        )
        revised_code = ProviderScope(
            provider_id=scope.provider_id, code_revision="2", origins=scope.origins
        )
        combined = ProviderScope(
            provider_id=scope.provider_id,
            origins=scope.origins | {Origin(id="elevation")},
        )
        for changed in (revised_data, revised_code, combined):
            assert changed != scope
            assert RecordIdentity(scope=changed, location_id="001") != RecordIdentity(
                scope=scope, location_id="001"
            )


@pytest.mark.parametrize("value", ["", 1, b"001", None])
def test_provider_and_record_ids_are_strict_nonempty_strings(value: object) -> None:
    with pytest.raises(ValidationError):
        ProviderScope.model_validate({"provider_id": value})
    with pytest.raises(ValidationError):
        RecordIdentity.model_validate(
            {"scope": ProviderScope(provider_id="tracts"), "location_id": value}
        )


def test_scope_code_revision_is_opaque_strict_text() -> None:
    assert ProviderScope(provider_id="tracts", code_revision="any revision / 1")
    with pytest.raises(ValidationError):
        ProviderScope.model_validate({"provider_id": "tracts", "code_revision": 1})
