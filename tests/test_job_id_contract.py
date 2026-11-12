"""Job identity validation is strict, format-independent, and service-free."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from lyra.contracts.inspection import ActiveResultOutput, TerminalResultOutput
from lyra.contracts.jobs import (
    AdminJobDetail,
    FailedJobResult,
    JobCreateResponse,
    JobEnvelope,
    JobId,
    JobListResponse,
    JobStatusInfo,
    TableJobResult,
)
from lyra.server.mcp.models import (
    DownloadResultOutput,
    GetJobResultInput,
    JobIdInput,
    RunMetricOutput,
)
from tests.job_id_helpers import INVALID_IDS
from tests.provenance_helpers import result_provenance

ADAPTER = TypeAdapter(JobId)
VALID_IDS = [
    "job-1",
    "not-a-uuid",
    "λ/结果?#",
    "lyra://results/job",
    " padded ",
    " ",
    "\t\n",
    "\u2003",
]


MODELS = [
    JobEnvelope,
    JobIdInput,
    GetJobResultInput,
    RunMetricOutput,
    DownloadResultOutput,
    TableJobResult,
    FailedJobResult,
    JobCreateResponse,
    JobStatusInfo,
    ActiveResultOutput,
    TerminalResultOutput,
]


@pytest.fixture(params=MODELS, ids=lambda model: model.__name__)
def model_payload(request: pytest.FixtureRequest) -> tuple[type[BaseModel], dict]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    status = {"status": "queued", "created_at": now, "updated_at": now}
    payloads = {
        JobIdInput: {},
        GetJobResultInput: {},
        RunMetricOutput: {},
        DownloadResultOutput: {
            "format": "json",
            "media_type": "application/json",
            "lifetime": {},
            "lyra_api": {
                "method": "GET",
                "url": "https://example.test/download",
                "authentication": {
                    "scheme": "Bearer",
                    "credential_env_var": "LYRA_AGENT_API_KEY",
                },
            },
        },
        JobEnvelope: {
            "accepted": result_provenance(
                {
                    "metric": "root",
                    "plugin": {"name": "fixture", "version": "1"},
                    "outputs": {"result": {"columns": []}},
                }
            ).accepted,
        },
        TableJobResult: {
            "outputs": {"result": []},
            "output_metadata": {"result": {"columns": []}},
        },
        FailedJobResult: {"error": {}},
        JobCreateResponse: {
            "metric": "root",
            "status": "queued",
            "links": {"self": "/jobs/job", "result": "/jobs/job/result"},
        },
        JobStatusInfo: status,
        ActiveResultOutput: status,
        TerminalResultOutput: {
            "status": "failed",
            "result_kind": "failed",
            "lifetime": {},
            "download": "/jobs/job/result",
        },
    }
    return request.param, payloads[request.param]


@pytest.mark.parametrize("job_id", VALID_IDS)
def test_job_id_preserves_nonempty_strings(job_id: str) -> None:
    assert ADAPTER.validate_python(job_id) == job_id
    assert ADAPTER.validate_json(json.dumps(job_id)) == job_id


@pytest.mark.parametrize("job_id", INVALID_IDS)
def test_job_id_rejects_without_conversion(job_id: object) -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(job_id)


@pytest.mark.parametrize("job_id", VALID_IDS)
def test_shared_models_preserve_job_identity(
    model_payload: tuple[type[BaseModel], dict], job_id: str
) -> None:
    model, payload = model_payload
    value = model.model_validate({**payload, "job_id": job_id})
    assert value.model_dump()["job_id"] == job_id
    restored = model.model_validate_json(value.model_dump_json())
    assert restored.model_dump()["job_id"] == job_id


@pytest.mark.parametrize("job_id", INVALID_IDS)
def test_shared_models_reject_invalid_job_identity(
    model_payload: tuple[type[BaseModel], dict], job_id: object
) -> None:
    model, payload = model_payload
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**payload, "job_id": job_id})
    errors = caught.value.errors(include_input=False)
    assert [error["loc"] for error in errors] == [("job_id",)]
    assert errors[0]["type"] == (
        "string_too_short" if isinstance(job_id, str) else "string_type"
    )


@pytest.mark.parametrize("model", MODELS)
def test_job_identity_schema(model: type[BaseModel]) -> None:
    for mode in ("validation", "serialization"):
        field = model.model_json_schema(mode=mode)["properties"]["job_id"]
        assert field["type"] == "string"
        assert field["minLength"] == 1
    assert ADAPTER.json_schema() == {"type": "string", "minLength": 1}


@pytest.mark.parametrize("job_id", INVALID_IDS)
@pytest.mark.parametrize("model", [JobListResponse, AdminJobDetail])
def test_admin_models_validate_nested_job_identity(
    model: type[BaseModel], job_id: object
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    status = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    payload = (
        {"jobs": [status]}
        if model is JobListResponse
        else {"snapshot": status, "queue": "interactive"}
    )
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    assert caught.value.errors(include_input=False)[0]["loc"][-1] == "job_id"
