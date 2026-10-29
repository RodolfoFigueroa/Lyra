import logging
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    LocalRunner,
    LocationContext,
    LocationContextAdapter,
    LocationResolver,
    PluginDefinition,
    RunContext,
    metric,
)
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.resources import ResourceScope

from lyra_app import worker
from tests.config_helpers import load_test_config
from tests.test_location_resolution import SELECTED, Provider, resolve, selection


def test_node_contexts_share_services_and_acquire_database_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "_runner_temp_base", lambda: tmp_path)
    database = Mock()
    build_database = Mock(return_value=database)
    native = Mock()
    native.meta = {}
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 1.0)
    job = JobEnvelope(job_id="shared-job", metric="public_metric", input={})
    metric = worker.build_run_context(job)
    operational_metric = worker.build_run_context(
        job, node="operational_metric", progress=metric.progress
    )
    build_database.assert_not_called()
    source = worker.build_run_context(job, node="source", progress=metric.progress)
    build_database.assert_not_called()
    assert not hasattr(source, "db")
    build_database.assert_not_called()
    for context, name in [
        (metric, "public_metric"),
        (operational_metric, "operational_metric"),
        (source, "source"),
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


@pytest.mark.parametrize("reporting", ["local", "worker"])
def test_location_adapter_preserves_reporting_and_resolves(
    reporting: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    logger = logging.LoggerAdapter(logging.getLogger(__name__), {})
    if reporting == "local":
        contexts: list[RunContext] = []

        @metric(
            name="select",
            description="Capture reporting services",
            outputs={"result": pa.DataFrameSchema({})},
        )
        def capture(context: RunContext) -> pd.DataFrame:
            contexts.append(context)
            return pd.DataFrame()

        LocalRunner.from_definitions(
            {"test": PluginDefinition(metrics=[capture])},
            cache_dir=tmp_path,
        ).run("select", {})
        base = contexts[0]
    else:
        config = load_test_config(tmp_path)
        monkeypatch.setattr(worker, "get_current_job", lambda: None)
        base = worker.WorkerRunContext(
            job_id="job",
            node="select",
            logger=logger,
            temp_dir=tmp_path,
            progress=worker.WorkerProgress("job", config.jobs),
        )
    progress = Mock(wraps=base.report_progress)
    monkeypatch.setattr(type(base), "report_progress", progress)
    scope = ResourceScope({"locations": Provider})
    context: LocationContext = LocationContextAdapter(
        base,
        LocationResolver(scope, selected=SELECTED, provider=Provider),
    )
    assert context.job_id == base.job_id
    assert context.node == base.node
    assert context.logger is base.logger
    assert context.temp_dir is base.temp_dir
    context.report_progress(
        stage="lookup", current=1, total=2, unit="batch", message="Resolving"
    )
    progress.assert_called_once_with(
        stage="lookup", current=1, total=2, unit="batch", message="Resolving"
    )
    with pytest.raises(ValueError, match="current"):
        context.report_progress(stage="lookup", current=-1)
    with scope.activate():
        assert (
            context.resolve_locations("test:units", "lot", ["b", "a", "b"])
            == resolve({"lots": selection("b", "a", "b")})["lots"]
        )
        assert context.resolve_locations("test:units", "lot", []).ids == []
