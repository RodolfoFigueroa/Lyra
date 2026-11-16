"""Retained domain lookups validate identity and never repair or execute work."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from rq.exceptions import DeserializationError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from lyra.contracts.config import JobsConfig
from lyra.contracts.errors import (
    InvalidResultReferenceError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.resources import ResourceScope
from lyra.runtime.results import ResultStore
from lyra.sdk import loading
from lyra.server import job_store
from lyra.server.job_store import InvalidJobResultError, JobStore
from lyra.server.results import ResultService
from tests.job_id_helpers import INVALID_IDS
from tests.retained_job_helpers import retain


@pytest.fixture
def retained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    path = tmp_path / "results.sqlite"
    jobs = JobStore(Mock(), JobsConfig())
    job = retain(
        path,
        monkeypatch,
        {
            "execution_id": "b" * 32,
            "result_ref": "a" * 32,
            "job_id": "job",
            "outputs": {"result": [{"value": 1}]},
            "output_metadata": {"result": {"columns": []}},
        },
        jobs=jobs,
    )
    return ResultService(jobs, path), job


@pytest.mark.parametrize("invalid", INVALID_IDS)
@pytest.mark.parametrize(
    "entry", ["read_job", "read_status", "get_job_detail", "read_observation"]
)
def test_invalid_ids_do_not_touch_storage(
    retained: tuple, monkeypatch: pytest.MonkeyPatch, invalid: object, entry: str
) -> None:
    service, _ = retained
    fetch = Mock(side_effect=AssertionError("invalid identifier reached storage"))
    monkeypatch.setattr(job_store.Job, "fetch", fetch)
    target = service if entry == "read_observation" else service.jobs
    with pytest.raises(ValidationError):
        getattr(target, entry)(invalid)
    fetch.assert_not_called()


@pytest.mark.parametrize("missing", ["absent", "expired"])
def test_missing_targets(
    retained: tuple, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    service, job = retained
    if missing == "absent":
        monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=NoSuchJobError))
    else:
        service.jobs.connection.ttl.return_value = -2
        job.args = ()  # Expired data needs no acceptance or result validation.
    assert service.read_observation("job") is None
    assert service.jobs.read_status("job") is None


@pytest.mark.parametrize(
    "damage", ["requested_identity", "accepted_identity", "acceptance", "arguments"]
)
@pytest.mark.parametrize("entry", ["read_job", "read_status", "get_job_detail"])
def test_required_job_relationships(retained: tuple, damage: str, entry: str) -> None:
    service, job = retained
    if damage == "requested_identity":
        job.id = "another"
        job.args[0]["job_id"] = "another"
    elif damage == "accepted_identity":
        job.args[0]["job_id"] = "another"
    elif damage == "acceptance":
        job.args[0]["accepted"] = {}
    else:
        job.args = ()
    with pytest.raises(InvalidJobResultError):
        getattr(service.jobs, entry)("job")


@pytest.mark.parametrize(
    "state", [JobStatus.QUEUED, JobStatus.STARTED, JobStatus.FAILED, JobStatus.FINISHED]
)
def test_lifecycle_reads_are_targeted_and_read_only(
    retained: tuple, monkeypatch: pytest.MonkeyPatch, state: JobStatus
) -> None:
    service, job = retained
    job.get_status.return_value = state
    job.meta = {}  # Execution, progress and detailed failure metadata are optional.
    before = json.dumps(job.args)
    forbidden = Mock(side_effect=AssertionError("read performed forbidden work"))
    monkeypatch.setattr(loading, "load_installed_plugin_definition", forbidden)
    monkeypatch.setattr(ResourceScope, "get", forbidden)
    monkeypatch.setattr(GraphExecutor, "acquire_sources", forbidden)
    monkeypatch.setattr(GraphExecutor, "execute", forbidden)
    for name in ["enqueue", "list_job_statuses"]:
        monkeypatch.setattr(service.jobs, name, forbidden)
    # Only keyed TTL access is allowed on the storage connection.
    service.jobs.connection = Mock(spec=["ttl"], ttl=Mock(return_value=60))
    for name in [
        "rglob",
        "glob",
        "mkdir",
        "write_text",
        "write_bytes",
        "unlink",
        "rename",
    ]:
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    observation = service.read_observation("job")
    assert observation is not None
    assert (
        observation.snapshot.status
        == {
            JobStatus.QUEUED: "queued",
            JobStatus.STARTED: "running",
            JobStatus.FAILED: "failed",
            JobStatus.FINISHED: "succeeded",
        }[state]
    )
    inspection = service.inspect(observation)
    assert inspection.job_id == "job"
    if state in {JobStatus.FAILED, JobStatus.FINISHED}:
        result = service.read_result(observation)
        assert result.job_id == "job"
        assert result.status == observation.snapshot.status
        if state == JobStatus.FINISHED:
            assert result.outputs == {"result": [{"value": 1}]}
    assert json.dumps(job.args) == before
    assert job.meta == {}
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "error", [InvalidResultReferenceError, ResultNotFoundError, ResultIntegrityError]
)
def test_required_retained_relationships(
    retained: tuple, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    service, _ = retained
    with ResultStore.open(service.result_store_path) as reader:
        monkeypatch.setattr(reader, "read_result", Mock(side_effect=error("a" * 32)))
    assert service.jobs.read_status("job").status == "succeeded"
    with pytest.raises(InvalidJobResultError):
        service.read_observation("job")


@pytest.mark.parametrize("status", [None, JobStatus.CANCELED, JobStatus.STOPPED])
def test_retained_unsupported_state_is_integrity_failure(
    retained: tuple, status: JobStatus | None
) -> None:
    service, job = retained
    job.get_status.return_value = status
    with pytest.raises(InvalidJobResultError, match="lifecycle"):
        service.read_observation("job")
    with pytest.raises(InvalidJobResultError, match="lifecycle"):
        service.jobs.read_status("job")


@pytest.mark.parametrize(
    "error", [ValueError, TypeError, KeyError, DeserializationError]
)
def test_corrupt_native_record_is_integrity_failure(
    retained: tuple, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    service, _ = retained
    monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=error))
    with pytest.raises(InvalidJobResultError):
        service.read_observation("job")


def test_corrupt_serialized_acceptance_is_integrity_failure(
    retained: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = retained
    native = Job("job", connection=Mock(), serializer=JSONSerializer)
    native.data = b"broken"
    monkeypatch.setattr(native, "get_status", Mock(return_value=JobStatus.QUEUED))
    monkeypatch.setattr(job_store.Job, "fetch", Mock(return_value=native))
    with pytest.raises(InvalidJobResultError, match="acceptance"):
        service.read_observation("job")


@pytest.mark.parametrize("entry", ["read_job", "read_status", "get_job_detail"])
@pytest.mark.parametrize("field", ["execution_id", "result_ref"])
@pytest.mark.parametrize("damage", ["missing", "null", "malformed", "no_record"])
def test_invalid_success_metadata_rejects_observation(
    retained: tuple, entry: str, field: str, damage: str
) -> None:
    service, job = retained
    payload = job.latest_result.return_value.return_value
    if damage == "no_record":
        job.latest_result.return_value = None
    elif damage == "missing":
        del payload[field]
    else:
        payload[field] = None if damage == "null" else "invalid"
    with pytest.raises(InvalidJobResultError):
        getattr(service.jobs, entry)("job")


@pytest.mark.parametrize("entry", ["read_job", "read_status", "get_job_detail"])
@pytest.mark.parametrize(
    "error", [ValueError, TypeError, KeyError, DeserializationError]
)
def test_corrupt_serialized_success_is_integrity_failure(
    retained: tuple, entry: str, error: type[Exception]
) -> None:
    service, job = retained
    cause = error("invalid serialized success metadata")
    job.latest_result.side_effect = cause
    with pytest.raises(InvalidJobResultError) as caught:
        getattr(service.jobs, entry)("job")
    assert caught.value.__cause__ is cause
