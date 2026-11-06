"""Jobs and computation lookups share immutable native output generations."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from rq.job import JobStatus

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobRunProvenance, TableJobResult
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.cache import TableCache
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import artifacts, job_store, worker
from lyra.server.job_store import InvalidJobResultError
from lyra.server.results import ResultUnavailableError
from tests.queue_helpers import accepted_envelope
from tests.worker_graph_integration_helpers import (
    VALUES,
    install_definitions,
    native_job,
)


def test_jobs_share_artifact_and_inspect_without_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame({"value": range(12)})

    _, runtime = install_definitions(
        tmp_path, monkeypatch, {"plugin": PluginDefinition(metrics=[root])}
    )
    native = native_job(monkeypatch)
    envelope = accepted_envelope(
        job_id=native.id, metric="root", inputs={}, catalog=runtime.catalog.compiled
    )
    first = worker.run_metric_task(envelope.model_dump(mode="json"))
    second = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert first == second
    assert list(first) == ["artifact_id"]
    assert calls == [True]
    now = datetime.now(UTC)
    native.args = (envelope.model_dump(mode="json"),)
    native.key = f"rq:job:{native.id}"
    native.created_at = native.started_at = native.ended_at = now
    native.get_status = Mock(return_value=JobStatus.FINISHED)
    native.latest_result = Mock(return_value=SimpleNamespace(return_value=first))
    monkeypatch.setattr(job_store.Job, "fetch", Mock(return_value=native))
    monkeypatch.setattr(runtime.jobs.connection, "ttl", Mock(return_value=60))
    read = runtime.results.artifacts.read
    monkeypatch.setattr(
        runtime.results.artifacts,
        "read",
        Mock(side_effect=AssertionError("inspection read full tables")),
    )
    observation = runtime.results.read_observation(native.id)
    assert observation is not None
    inspection = runtime.results.inspect(observation)
    assert isinstance(inspection, TerminalResultOutput)
    assert inspection.outputs["answer"].metadata is not None
    assert inspection.outputs["answer"].metadata.row_count == 12
    assert inspection.outputs["answer"].truncation.omitted_rows == 7
    assert observation.provenance == JobRunProvenance(
        accepted=envelope.accepted, execution=native.meta["execution"]
    )
    monkeypatch.setattr(runtime.results.artifacts, "read", read)
    full = runtime.results.read_result(observation)
    assert isinstance(full, TableJobResult)
    assert len(full.outputs["answer"]) == 12
    # Clearing computation lookups does not make a retained job unavailable.
    for pointer in (runtime.config.artifact_dir / "cache").iterdir():
        if not pointer.name.endswith(".lock"):
            pointer.unlink()
    assert runtime.results.read_result(observation) == full
    reference = first["artifact_id"]
    assert isinstance(reference, str)
    bundle = runtime.results.artifacts.directory / reference
    (bundle / "0.arrow").write_bytes(b"broken")
    with pytest.raises(InvalidJobResultError):
        runtime.results.read_result(observation)
    (bundle / "0.arrow").unlink()
    with pytest.raises(ResultUnavailableError):
        runtime.results.read_observation(native.id)


def test_required_root_publication_failure_fails_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    _, runtime = install_definitions(
        tmp_path, monkeypatch, {"plugin": PluginDefinition(metrics=[root])}
    )
    native = native_job(monkeypatch)
    envelope = accepted_envelope(
        job_id=native.id, metric="root", inputs={}, catalog=runtime.catalog.compiled
    )
    failure = Mock()
    monkeypatch.setattr(worker, "_save_failure", failure)
    monkeypatch.setattr(ArtifactStore, "write", Mock(side_effect=OSError("full")))
    with pytest.raises(OSError, match="full"):
        worker.run_metric_task(envelope.model_dump(mode="json"))
    failure.assert_called_once()


def test_prune_preserves_jobs_and_cache_and_reclaims_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TableCache(tmp_path)
    table = {"value": pd.DataFrame({"value": [1]})}
    retained = cache.store.write(table, {"value": VALUES})
    cached = cache.write("computation", table, {"value": VALUES})
    orphan = cache.store.write(table, {"value": VALUES})
    connection = Mock(
        scan_iter=Mock(return_value=[b"rq:job:one", b"rq:job:two"]),
        type=Mock(return_value=b"hash"),
    )
    job = Mock(
        get_status=Mock(return_value=JobStatus.FINISHED),
        latest_result=Mock(
            return_value=SimpleNamespace(return_value={"artifact_id": retained})
        ),
    )
    monkeypatch.setattr(artifacts.Job, "fetch", Mock(return_value=job))
    dry = artifacts.prune(tmp_path, connection, dry_run=True)
    assert dry["bundles"] == 1
    assert (cache.store.directory / orphan).exists()
    assert artifacts.prune(tmp_path, connection) == dry
    assert (cache.store.directory / retained).exists()
    assert (cache.store.directory / str(cached)).exists()
    assert not (cache.store.directory / orphan).exists()
    cleared = artifacts.prune(tmp_path, connection, clear_cache=True)
    assert cleared["lookups"] == 1
    assert (cache.store.directory / retained).exists()
    assert not (cache.store.directory / str(cached)).exists()
    connection.scan_iter.return_value = []
    assert artifacts.prune(tmp_path, connection)["bundles"] == 1


@pytest.mark.parametrize("result", [None, {}, {"artifact_id": "invalid"}])
def test_prune_collects_all_references_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict | None
) -> None:
    store = ArtifactStore(tmp_path)
    reference = store.write({"value": pd.DataFrame({"value": [1]})}, {"value": VALUES})
    connection = Mock(scan_iter=Mock(side_effect=OSError("unavailable")))
    with pytest.raises(OSError, match="unavailable"):
        artifacts.prune(tmp_path, connection, clear_cache=True)
    assert (store.directory / reference).exists()
    connection.scan_iter.side_effect = None
    connection.scan_iter.return_value = [b"rq:job:one"]
    connection.type.return_value = b"hash"
    monkeypatch.setattr(
        artifacts.Job,
        "fetch",
        Mock(
            return_value=Mock(
                get_status=Mock(return_value=JobStatus.FINISHED),
                latest_result=Mock(
                    return_value=None
                    if result is None
                    else SimpleNamespace(return_value=result)
                ),
            )
        ),
    )
    with pytest.raises(
        ValueError, match=r"Cannot inspect retained result|validation error"
    ):
        artifacts.prune(tmp_path, connection)
    assert (store.directory / reference).exists()


def test_worker_retains_artifact_when_lookup_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    _, runtime = install_definitions(
        tmp_path, monkeypatch, {"plugin": PluginDefinition(metrics=[root])}
    )
    native = native_job(monkeypatch)
    envelope = accepted_envelope(
        job_id=native.id, metric="root", inputs={}, catalog=runtime.catalog.compiled
    )
    original = ArtifactStore.write
    publications = []

    def publish(store: ArtifactStore, outputs: dict, schemas: dict) -> str:
        reference = original(store, outputs, schemas)
        publications.append(reference)
        return reference

    monkeypatch.setattr(ArtifactStore, "write", publish)
    monkeypatch.setattr(
        Path, "replace", Mock(side_effect=OSError("lookup unavailable"))
    )
    result = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert publications == [result["artifact_id"]]
    store = ArtifactStore(runtime.config.artifact_dir)
    assert store.read(publications[0])["answer"].value.tolist() == [1]
    assert {path.name for path in store.directory.iterdir()} == set(publications)


def test_prune_reclaims_superseded_and_interrupted_writes(tmp_path: Path) -> None:
    cache = TableCache(tmp_path)
    table = {"value": pd.DataFrame({"value": [1]})}
    old = cache.write("key", table, {"value": VALUES})
    current = cache.write("key", table, {"value": VALUES})
    assert old is not None
    assert current is not None
    assert old != current
    temporary = [
        cache.directory / ".writing-lookup",
        cache.store.directory / ".writing-bundle",
    ]
    for path in temporary:
        path.mkdir()
        (path / "partial").write_bytes(b"incomplete")
    connection = Mock(scan_iter=Mock(return_value=[]))
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    dry = artifacts.prune(tmp_path, connection, dry_run=True)
    assert dry["bundles"] == 1
    assert dry["temporary"] == 2
    assert dry["lookups"] == 0
    assert dry["bytes"] > 0
    cleared = artifacts.prune(tmp_path, connection, dry_run=True, clear_cache=True)
    assert cleared["bundles"] == 2
    assert cleared["lookups"] == 1
    assert cleared["temporary"] == 2
    assert before == {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert artifacts.prune(tmp_path, connection) == dry
    assert {path.name for path in cache.store.directory.iterdir()} == {current}
    assert cache.reference("key") == current
    assert all(not path.exists() for path in temporary)
    assert artifacts.prune(tmp_path, connection) == {
        "bundles": 0,
        "temporary": 0,
        "lookups": 0,
        "bytes": 0,
    }


@pytest.mark.parametrize("clear_cache", [False, True])
@pytest.mark.parametrize("failure", ["invalid", "unreadable"])
def test_prune_lookup_error_prevents_all_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, clear_cache: bool, failure: str
) -> None:
    cache = TableCache(tmp_path)
    reference = cache.write(
        "key", {"value": pd.DataFrame({"value": [1]})}, {"value": VALUES}
    )
    assert reference is not None
    scratch = cache.directory / ".writing-abandoned"
    scratch.mkdir()
    lookup = cache.directory / "key"
    if failure == "invalid":
        lookup.write_text("invalid")
        error = ValueError
    else:
        monkeypatch.setattr(Path, "read_text", Mock(side_effect=OSError("unavailable")))
        error = OSError
    with pytest.raises(error):
        artifacts.prune(
            tmp_path, Mock(scan_iter=Mock(return_value=[])), clear_cache=clear_cache
        )
    assert (cache.store.directory / reference).exists()
    assert lookup.exists()
    assert scratch.exists()


@pytest.mark.parametrize("failure", ["lookup", "bundle"])
def test_interrupted_prune_is_safe_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    cache = TableCache(tmp_path)
    table = {"value": pd.DataFrame({"value": [1]})}
    references = {cache.write(key, table, {"value": VALUES}) for key in ("one", "two")}
    assert None not in references
    connection = Mock(scan_iter=Mock(return_value=[]))
    removed = []
    with monkeypatch.context() as patch:
        if failure == "lookup":
            original_unlink = Path.unlink

            def unlink(path: Path, *, missing_ok: bool = False) -> None:
                if removed:
                    message = "interrupted lookup deletion"
                    raise OSError(message)
                original_unlink(path, missing_ok=missing_ok)
                removed.append(path)

            patch.setattr(Path, "unlink", unlink)
        else:
            original_rmtree = artifacts.shutil.rmtree

            def rmtree(path: Path) -> None:
                assert not any(cache.directory.iterdir())
                if removed:
                    message = "interrupted bundle deletion"
                    raise OSError(message)
                original_rmtree(path)
                removed.append(path)

            patch.setattr(artifacts.shutil, "rmtree", rmtree)
        with pytest.raises(OSError, match="interrupted"):
            artifacts.prune(tmp_path, connection, clear_cache=True)
    assert len(removed) == 1
    if failure == "lookup":
        assert {path.name for path in cache.store.directory.iterdir()} == references
        remaining = list(cache.directory.iterdir())
        assert len(remaining) == 1
        assert (cache.store.directory / remaining[0].read_text()).is_dir()
    else:
        assert len(list(cache.store.directory.iterdir())) == 1
    artifacts.prune(tmp_path, connection, clear_cache=True)
    assert list(cache.directory.iterdir()) == []
    assert list(cache.store.directory.iterdir()) == []
