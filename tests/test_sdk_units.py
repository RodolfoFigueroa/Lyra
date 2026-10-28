"""The unit vocabulary agrees across validation, schemas, and serialized contracts."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate
from lyra.sdk import TableColumn, Unit
from lyra.sdk.models.job import JobStatusInfo, ResultDescriptor, TableJobResult
from lyra.sdk.models.plugin import PluginManifest
from pydantic import ValidationError

from lyra_app import job_store
from lyra_app.routes import jobs
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.test_api_client_jobs import _result_descriptor_response

BASE = {"name": "value", "type": "number", "description": "Test quantity."}


@pytest.mark.parametrize("unit", [*Unit, None])
def test_units_round_trip_through_python_json_schema_and_manifest(
    unit: Unit | None,
) -> None:
    column = TableColumn(name="value", type="number", description="Test", unit=unit)
    raw = {**BASE, "unit": unit.value if unit is not None else None}
    assert TableColumn.model_validate(raw).unit is unit
    assert TableColumn.model_validate_json(json.dumps(raw)).unit is unit
    validate(raw, TableColumn.model_json_schema())
    validate(raw, TableColumn.model_json_schema(mode="serialization"))
    assert column.model_dump(mode="json")["unit"] == raw["unit"]
    payload = plugin_manifest(metric_manifest())
    payload["nodes"][0]["outputs"]["light_metric"]["columns"] = [raw]
    manifest = PluginManifest.model_validate(payload)
    serialized = manifest.model_dump_json()
    assert (
        json.loads(serialized)["nodes"][0]["outputs"]["light_metric"]["columns"][0][
            "unit"
        ]
        == raw["unit"]
    )
    assert PluginManifest.model_validate_json(serialized) == manifest


@pytest.mark.parametrize("unit", ["", "M2", "celsius", "unknown", "not_applicable", 1])
def test_unknown_units_fail_model_and_schema(unit: object) -> None:
    payload = {**BASE, "unit": unit}
    with pytest.raises(ValidationError):
        TableColumn.model_validate(payload)
    with pytest.raises(SchemaValidationError):
        validate(payload, TableColumn.model_json_schema())


def test_unit_is_required_even_when_not_applicable() -> None:
    with pytest.raises(ValidationError, match="unit"):
        TableColumn.model_validate(BASE)
    with pytest.raises(SchemaValidationError):
        validate(BASE, TableColumn.model_json_schema())


@pytest.mark.parametrize("column_type", ["integer", "number", "string", "boolean"])
def test_null_unit_is_independent_of_column_type(column_type: str) -> None:
    column = TableColumn.model_validate({**BASE, "type": column_type, "unit": None})
    assert column.unit is None
    assert column.nullable is False


def test_area_derivations_are_not_declarations() -> None:
    with pytest.raises(ValidationError, match="derivations"):
        TableColumn.model_validate({**BASE, "unit": "m2", "derivations": []})


@pytest.mark.parametrize("unit", [Unit.COUNT, Unit.COUNT_PER_SQUARE_KILOMETRE, None])
def test_rest_result_descriptor_preserves_unit(
    unit: Unit | None,
) -> None:
    payload = _result_descriptor_response()
    payload["outputs"]["result"]["declaration"]["columns"][0]["unit"] = unit
    payload["provenance"]["outputs"]["result"]["columns"][0]["unit"] = unit
    descriptor = ResultDescriptor.model_validate(payload)
    store = Mock(spec=job_store.JobStore)
    store.observe_job = AsyncMock(
        return_value=job_store.JobObservation(
            snapshot=JobStatusInfo(
                job_id="job-1",
                status="succeeded",
                created_at=datetime.now(UTC),
                updated_at=descriptor.completed_at,
            ),
            result=TableJobResult(
                job_id="job-1",
                outputs={"result": []},
                output_metadata={"result": descriptor.outputs["result"].declaration},
                provenance=descriptor.provenance,
            ),
            provenance=descriptor.provenance,
            lifetime=descriptor.lifetime,
        )
    )
    response = asyncio.run(jobs.get_job_result_descriptor("job-1", store))
    wire = json.loads(bytes(response.body))
    assert wire["outputs"]["result"]["declaration"]["columns"][0]["unit"] == unit
    assert wire["provenance"]["outputs"]["result"]["columns"][0]["unit"] == unit
    assert ResultDescriptor.model_validate(wire).provenance == descriptor.provenance
