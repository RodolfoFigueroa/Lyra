"""Retained domain lookups validate identity and never repair or execute work."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from rq.exceptions import DeserializationError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from lyra.contracts.config import JobsConfig
from lyra.runtime import loading
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.resources import ResourceScope
from lyra.server import job_store
from lyra.server.job_store import InvalidJobResultError, JobStore
from lyra.server.results import ResultService
from tests.job_id_helpers import INVALID_IDS
from tests.retained_job_helpers import retain


@pytest.fixture
def retained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    artifacts = ArtifactStore(tmp_path)
    jobs = JobStore(Mock(), JobsConfig())
    job = retain(
        artifacts,
        monkeypatch,
        {
            "job_id": "job",
            "outputs": {"result": [{"value": 1}]},
            "output_metadata": {"result": {"columns": []}},
        },
        jobs=jobs,
    )
    return ResultService(jobs, artifacts), job


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
    monkeypatch.setattr(loading, "load_bound_plugin", forbidden)
    monkeypatch.setattr(ResourceScope, "get", forbidden)
    monkeypatch.setattr(GraphExecutor, "acquire_sources", forbidden)
    monkeypatch.setattr(GraphExecutor, "execute", forbidden)
    for name in ["enqueue", "list_job_statuses"]:
        monkeypatch.setattr(service.jobs, name, forbidden)
    # Only keyed TTL access is allowed on the storage connection.
    service.jobs.connection = Mock(spec=["ttl"], ttl=Mock(return_value=60))
    for name in ["write", "write_locations", "read_locations"]:
        monkeypatch.setattr(service.artifacts, name, forbidden)
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
    original_iterdir = Path.iterdir

    def targeted_iterdir(path: Path) -> Iterator[Path]:
        assert path.parent == service.artifacts.directory
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", targeted_iterdir)
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
    "damage",
    [
        "reference",
        "bundle",
        "manifest",
        "inspection",
        "table",
        "names",
        "corrupt_table",
        "corrupt_inspection",
    ],
)
def test_required_artifact_relationships(retained: tuple, damage: str) -> None:
    service, job = retained
    observation = service.read_observation("job")
    bundle = service.artifacts.directory / observation.artifact_id
    if damage == "reference":
        job.latest_result.return_value = None
    elif damage == "bundle":
        job.latest_result.return_value.return_value = {"artifact_id": "f" * 32}
    elif damage in {"manifest", "inspection", "table"}:
        (
            bundle
            / {
                "manifest": "manifest.json",
                "inspection": "inspection.json",
                "table": "0.arrow",
            }[damage]
        ).unlink()
    elif damage == "names":
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["outputs"] = {"other": {}}
        manifest_path.write_text(json.dumps(manifest))
        inspection_path = bundle / "inspection.json"
        inspection = json.loads(inspection_path.read_text())
        inspection_path.write_text(
            json.dumps({"other": next(iter(inspection.values()))})
        )
    else:
        (
            bundle / ("0.arrow" if damage == "corrupt_table" else "inspection.json")
        ).write_bytes(b"broken")
    # Status observes lifecycle without loading artifact data.
    if damage != "names":
        assert service.jobs.read_status("job").status == "succeeded"
    if damage == "corrupt_table":
        with pytest.raises(InvalidJobResultError):
            service.read_result(observation)
    else:
        with pytest.raises(InvalidJobResultError):
            service.read_observation("job")
    if damage in {"manifest", "inspection", "table", "corrupt_inspection"}:
        with pytest.raises(InvalidJobResultError):
            service.read_result(observation)


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
