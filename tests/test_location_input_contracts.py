"""Retained location descriptions and strict lookup context without input values."""

from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from lyra.contracts.errors import (
    InvalidPaginationError,
    LocationInputUnavailableError,
    ResultIntegrityError,
    ResultNotFoundError,
    RowTooLargeError,
)
from lyra.contracts.references import (
    validate_job_id,
    validate_retrieval_name,
)
from lyra.contracts.results import LocationInputDescription, ResultDescription
from lyra.contracts.retrieval_errors import decode_error, error_payload
from lyra.contracts.retrieval_paths import table_rows_path
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.mcp.models import DownloadTableInput
from tests.result_description_helpers import location_descriptions

_REFERENCE = "a" * 32


def test_description_round_trip_keeps_schema_and_scope_roles() -> None:
    inputs = location_descriptions()
    original = ResultDescription(
        job_id=_REFERENCE,
        metric_name="metric",
        parameters={},
        outputs={},
        location_inputs=inputs,
    )
    restored = ResultDescription.model_validate_json(original.model_dump_json())
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    direct = restored.location_inputs["blocks"]
    correspondence = restored.location_inputs["nearby_trees"]
    assert direct.kind == "provider"
    assert direct.source_scope is None
    assert correspondence.kind == "correspondence"
    assert correspondence.source_scope == direct.provider_scope
    assert correspondence.provider_scope.provider_id == "trees"
    assert direct.provider_scope.description == "Survey places"
    assert correspondence.source_scope is not None
    assert correspondence.source_scope.description == "Survey places"
    assert correspondence.provider_scope.description == "Survey trees"
    origin = next(iter(direct.provider_scope.origins))
    assert (origin.id, origin.revision, origin.description) == ("survey", "1", "Survey")
    assert direct.provider_scope.code_revision == "survey"
    assert direct.table_schema.columns[1].nullable
    assert direct.table_schema.columns[1].unit == "meter"
    assert direct.table_schema.columns[-1].parameters["crs"] == "EPSG:32614"
    assert restored.location_inputs["known_empty"].availability == "available"
    assert (
        restored.location_inputs["reference_blocks"].availability == "not_materialized"
    )
    restored.location_inputs["blocks"].table_schema.columns.clear()
    assert original.location_inputs["blocks"].table_schema.columns


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "metric"},
        {"availability": "empty"},
        {"kind": "correspondence"},
        {"source_scope": {"provider_id": "source"}},
        {"provider_scope": {"provider_id": 1}},
        {"table_schema": {}},
        {"intervention_id": "extra"},
    ],
)
def test_invalid_location_description_is_rejected(changes: dict) -> None:
    payload = location_descriptions()["blocks"].model_dump(mode="json")
    payload.update(changes)
    with pytest.raises(ValidationError):
        LocationInputDescription.model_validate(payload)


@pytest.mark.parametrize("name", [None, 1, True, b"blocks", ["blocks"]])
def test_lookup_names_are_strict_across_boundaries(name: object) -> None:
    with pytest.raises(ValidationError):
        validate_retrieval_name(name)
    with pytest.raises(ValidationError):
        DownloadTableInput.model_validate({"job_id": _REFERENCE, "output_name": name})
    reader = ResultStore(Mock(spec=SQLiteStore))
    with pytest.raises(ValidationError):
        reader.read_table(_REFERENCE, cast("str", name))
    with pytest.raises(ValidationError):
        table_rows_path(_REFERENCE, cast("str", name))


@pytest.mark.parametrize("name", [1, b"blocks", None])
def test_input_inventory_does_not_coerce_names(name: object) -> None:
    with pytest.raises(ValidationError):
        ResultDescription.model_validate(
            {
                "job_id": _REFERENCE,
                "metric_name": "metric",
                "parameters": {},
                "outputs": {},
                "location_inputs": {name: location_descriptions()["blocks"]},
            }
        )


@pytest.mark.parametrize(
    "reference", [None, 123, b"a" * 32, "A" * 32, "result_example"]
)
def test_invalid_reference_keeps_input_context(reference: object) -> None:
    with pytest.raises(ValidationError):
        validate_job_id(reference)


@pytest.mark.parametrize("output_name", [None, "table"])
def test_existing_lookup_payloads_keep_their_shape(output_name: str | None) -> None:
    for error in (
        ResultNotFoundError(_REFERENCE, output_name),
        ResultIntegrityError(_REFERENCE, output_name),
        InvalidPaginationError(_REFERENCE, output_name, "cursor"),
        RowTooLargeError(_REFERENCE, output_name, "https://example.test/download"),
    ):
        assert error.input_name is None
        payload = error_payload(error).model_dump()
        assert payload["output_name"] == output_name
        assert "input_name" not in payload


@pytest.mark.parametrize("field", ["code", "message", "job_id", "input_name"])
def test_unavailable_error_requires_its_fields(field: str) -> None:
    payload = error_payload(
        LocationInputUnavailableError(_REFERENCE, "blocks")
    ).model_dump()
    assert set(payload) == {"code", "message", "job_id", "input_name"}
    del payload[field]
    assert decode_error(payload, status=409) is None


@pytest.mark.parametrize("value", [None, 1, b"blocks"])
def test_error_input_names_are_strict(value: object) -> None:
    payload = error_payload(
        LocationInputUnavailableError(_REFERENCE, "blocks")
    ).model_dump()
    payload["input_name"] = value
    assert decode_error(payload, status=409) is None


def test_unavailable_error_requires_matching_status() -> None:
    payload = error_payload(
        LocationInputUnavailableError(_REFERENCE, "blocks")
    ).model_dump()
    assert decode_error(payload, status=404) is None


def test_describe_result_whole_lookup_context() -> None:
    store = Mock(spec=SQLiteStore)
    reader = ResultStore(store)
    with pytest.raises(ValidationError):
        reader.describe_result("bad")
    assert not store.mock_calls
