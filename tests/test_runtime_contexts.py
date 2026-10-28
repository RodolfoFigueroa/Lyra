import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from lyra.sdk.models.job import JobEnvelope

from lyra_app import worker
from tests.config_helpers import load_test_config


def test_node_contexts_share_operational_services_without_leaking_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "_runner_temp_base", lambda: tmp_path)
    database = Mock()
    build_database = Mock(return_value=database)
    monkeypatch.setattr(worker, "_build_db_context", build_database)
    native = Mock()
    native.meta = {}
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 1.0)
    job = JobEnvelope(job_id="shared-job", metric="public_metric", input={})
    metric = worker.build_run_context(job)
    operational_dataset = worker.build_run_context(
        job, node="operational_dataset", progress=metric.progress
    )
    assert not hasattr(metric, "db")
    assert not hasattr(operational_dataset, "db")
    build_database.assert_not_called()
    dataset = worker.build_dataset_context(job, node="source", progress=metric.progress)
    build_database.assert_not_called()
    assert dataset.db is database
    assert dataset.db is database
    build_database.assert_called_once()
    for context, name in [
        (metric, "public_metric"),
        (operational_dataset, "operational_dataset"),
        (dataset, "source"),
    ]:
        assert context.node == name
        assert context.job_id == job.job_id
        assert context.temp_dir == tmp_path / job.job_id
        assert context.temp_dir.is_dir()
        assert isinstance(context.logger, logging.LoggerAdapter)
        assert context.logger.extra == {
            "structured_fields": {"job_id": job.job_id, "node": name}
        }
        assert not hasattr(context, "metric")
        context.report_progress(stage=name, current=1)
    assert native.save_meta.call_count == 1
    monkeypatch.setattr(worker.time, "monotonic", lambda: 100.0)
    metric.flush_progress()
    assert native.save_meta.call_count == 2
    assert native.meta["progress"]["stage"] == "source"


def test_progress_failure_is_best_effort_and_values_are_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 1.0)
    native = Mock()
    native.meta = {}
    native.save_meta.side_effect = worker.RedisError("offline")
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    context = worker.WorkerRunContext(
        job_id="test",
        node="work",
        logger=logging.getLogger(__name__),
        temp_dir=tmp_path,
        progress=worker.WorkerProgress("test", config.jobs),
    )
    context.report_progress(stage="work", current=1, total=2)
    context.report_progress(stage="work", current=2, total=2)
    context.flush_progress()
    assert native.save_meta.call_count == 1
    with pytest.raises(ValueError, match="current"):
        context.report_progress(stage="work", current=float("nan"))
