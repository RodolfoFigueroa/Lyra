"""Successful job responses retain strict identifiers without loading values."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult


@pytest.fixture(params=[JobStatusInfo, TableJobResult, TerminalResultOutput])
def success_payload(request: pytest.FixtureRequest) -> tuple[type[BaseModel], dict]:
    now = datetime.now(UTC)
    model = request.param
    fields = {
        JobStatusInfo: {"created_at": now, "updated_at": now},
        TableJobResult: {
            "outputs": {"result": []},
            "output_metadata": {"result": {"columns": []}},
        },
        TerminalResultOutput: {
            "result_kind": "table",
            "lifetime": {},
            "download": "/jobs/job/result/download",
        },
    }
    return model, {
        "job_id": "job",
        "status": "succeeded",
        "execution_id": "b" * 32,
        "result_ref": "a" * 32,
        **fields[model],
    }


def test_successful_identifiers_round_trip(
    success_payload: tuple[type[BaseModel], dict],
) -> None:
    model, payload = success_payload
    value = model.model_validate(payload)
    restored = model.model_validate_json(value.model_dump_json()).model_dump()
    assert restored["execution_id"] == payload["execution_id"]
    assert restored["result_ref"] == payload["result_ref"]


@pytest.mark.parametrize("field", ["execution_id", "result_ref"])
@pytest.mark.parametrize(
    "invalid", [None, "", "job", "a" * 31, "A" * 32, " " + "a" * 32, b"a" * 32, 42]
)
def test_successful_identifiers_are_strict(
    success_payload: tuple[type[BaseModel], dict], field: str, invalid: object
) -> None:
    model, payload = success_payload
    payload[field] = invalid
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("field", ["execution_id", "result_ref"])
def test_successful_identifiers_are_required(
    success_payload: tuple[type[BaseModel], dict], field: str
) -> None:
    model, payload = success_payload
    del payload[field]
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
@pytest.mark.parametrize("field", ["execution_id", "result_ref"])
def test_other_states_have_no_successful_identifiers(status: str, field: str) -> None:
    now = datetime.now(UTC)
    payload = {
        "job_id": "job",
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    snapshot = JobStatusInfo.model_validate(payload)
    assert snapshot.execution_id is None
    assert snapshot.result_ref is None
    with pytest.raises(ValidationError):
        JobStatusInfo.model_validate({**payload, field: "a" * 32})
    if status == "failed":
        terminal = {
            "job_id": "job",
            "status": status,
            "result_kind": "failed",
            "lifetime": {},
            "download": "/jobs/job/result/download",
        }
        inspection = TerminalResultOutput.model_validate(terminal)
        assert inspection.execution_id is None
        assert inspection.result_ref is None
        with pytest.raises(ValidationError):
            TerminalResultOutput.model_validate({**terminal, field: "a" * 32})
