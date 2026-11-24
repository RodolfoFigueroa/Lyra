import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from lyra.runtime.local import LocalRunner
from lyra.runtime.resources import ResourceScope
from lyra.sdk.context import RunContext
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO
from lyra.server import worker
from test_support.table_fixtures import value_frame, value_schema
from tests.config_helpers import load_test_config


def test_node_contexts_share_progress_and_expose_job_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "_runner_temp_base", lambda: tmp_path)
    native = Mock()
    native.meta = {}
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 1.0))
    job_id = "shared-job"
    setup = Mock()

    class Database(BaseIO):
        def setup(self) -> None:
            setup(self.resource_name)

    resources = ResourceScope({"database": Database})
    with resources.activate():
        metric = worker.build_run_context(job_id, node="public_metric")
        operational_metric = worker.build_run_context(
            job_id, node="operational_metric", progress=metric.progress
        )
        source = worker.build_run_context(
            job_id, node="source", progress=metric.progress
        )
        setup.assert_not_called()
        resources.get("database")
        setup.assert_called_once_with("database")
    for context, name in [
        (metric, "public_metric"),
        (operational_metric, "operational_metric"),
        (source, "source"),
    ]:
        assert context.node == name
        assert context.job_id == job_id
        assert context.temp_dir == tmp_path / job_id
        assert context.temp_dir.is_dir()
        assert isinstance(context.logger, logging.LoggerAdapter)
        assert context.logger.extra == {
            "structured_fields": {"job_id": job_id, "node": name}
        }
        context.report_progress(stage=name, current=1)
    assert native.save_meta.call_count == 1
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 100.0))
    metric.flush_progress()
    assert native.save_meta.call_count == 2
    assert native.meta["progress"]["stage"] == "source"


def test_progress_failure_is_best_effort_and_values_are_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 1.0))
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
def test_local_and_worker_contexts_validate_reporting(
    reporting: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    logger = logging.LoggerAdapter(logging.getLogger(__name__), {})
    if reporting == "local":
        contexts: list[RunContext] = []

        @metric(
            code_revision="1",
            name="select",
            description="Capture reporting services",
            outputs={"result": value_schema()},
        )
        def capture(context: RunContext) -> pd.DataFrame:
            contexts.append(context)
            return value_frame()

        LocalRunner.from_definitions(
            {"test": PluginDefinition(metrics=[capture])},
            result_store_path=tmp_path / "results.sqlite3",
        ).run("select", inputs={})
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
    context = base
    assert context.job_id
    assert context.node == "select"
    assert isinstance(context.logger, logging.Logger | logging.LoggerAdapter)
    assert isinstance(context.temp_dir, Path)
    context.report_progress(
        stage="lookup", current=1, total=2, unit="batch", message="Resolving"
    )
    progress.assert_called_once_with(
        stage="lookup", current=1, total=2, unit="batch", message="Resolving"
    )
    with pytest.raises(ValueError, match="current"):
        context.report_progress(stage="lookup", current=-1)
