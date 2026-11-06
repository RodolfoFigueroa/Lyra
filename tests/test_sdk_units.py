"""The unit vocabulary agrees across validation, schemas, and serialized contracts."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate
from pydantic import ValidationError

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.contracts.outputs import ColumnMetadata
from lyra.contracts.plugins import PluginManifest
from lyra.contracts.units import Unit
from lyra.server.result_projection import project_observation
from lyra.server.results import ResultService
from lyra.server.routes import jobs
from tests.client_helpers import _result_descriptor_response
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.provenance_helpers import result_provenance
from tests.result_helpers import result_observation

BASE = {"name": "value", "dtype": "float64", "description": "Test quantity."}


@pytest.mark.parametrize("unit", [*Unit, None])
def test_units_round_trip_through_python_json_schema_and_manifest(
    unit: Unit | None,
) -> None:
    column = ColumnMetadata(
        name="value", dtype="float64", description="Test", unit=unit
    )
    raw = {**BASE, "unit": unit.value if unit is not None else None}
    assert ColumnMetadata.model_validate(raw).unit is unit
    assert ColumnMetadata.model_validate_json(json.dumps(raw)).unit is unit
    validate(raw, ColumnMetadata.model_json_schema())
    validate(raw, ColumnMetadata.model_json_schema(mode="serialization"))
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
        ColumnMetadata.model_validate(payload)
    with pytest.raises(SchemaValidationError):
        validate(payload, ColumnMetadata.model_json_schema())


def test_unit_is_required_even_when_not_applicable() -> None:
    with pytest.raises(ValidationError, match="unit"):
        ColumnMetadata.model_validate(BASE)
    with pytest.raises(SchemaValidationError):
        validate(BASE, ColumnMetadata.model_json_schema())


@pytest.mark.parametrize("column_type", ["int64", "float64", "str", "bool"])
def test_null_unit_is_independent_of_column_type(column_type: str) -> None:
    column = ColumnMetadata.model_validate({**BASE, "dtype": column_type, "unit": None})
    assert column.unit is None
    assert column.nullable is False


def test_area_derivations_are_not_declarations() -> None:
    with pytest.raises(ValidationError, match="derivations"):
        ColumnMetadata.model_validate({**BASE, "unit": "m2", "derivations": []})


@pytest.mark.parametrize(
    "unit",
    [
        Unit.COUNT,
        Unit.COUNT_PER_SQUARE_KILOMETRE,
        Unit.COUNT_PER_HECTARE,
        Unit.COUNT_PER_THOUSAND_INHABITANTS,
        None,
    ],
)
def test_rest_result_descriptor_preserves_unit(
    unit: Unit | None,
) -> None:

    payload = _result_descriptor_response()
    declaration = payload["outputs"]["result"]["metadata"]["declaration"]
    declaration["columns"][0]["unit"] = unit
    provenance = result_provenance(
        {
            "metric": "heavy_metric",
            "outputs": {"result": declaration},
            "plugin": {"name": "fixture", "version": "1"},
        }
    )
    observation = result_observation(
        snapshot=JobStatusInfo(
            job_id="job-1",
            status="succeeded",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        result=TableJobResult(
            job_id="job-1",
            outputs={"result": []},
            output_metadata={"result": declaration},
            provenance=provenance,
        ),
    )
    service = Mock(spec=ResultService)
    service.observe_job = AsyncMock(return_value=observation)
    service.inspect.side_effect = lambda item: project_observation(
        item.snapshot.job_id, item
    )
    response = asyncio.run(jobs.inspect_job_result("job-1", service))
    wire = json.loads(bytes(response.body))
    assert (
        wire["outputs"]["result"]["metadata"]["declaration"]["columns"][0]["unit"]
        == unit
    )
    assert provenance.accepted.plan.output_metadata["result"].columns[0].unit == unit
    TerminalResultOutput.model_validate(wire)
