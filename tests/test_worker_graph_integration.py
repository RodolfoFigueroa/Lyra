"""Exercise submission and worker graphs with local plugins and fake infrastructure."""

import asyncio
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk import (
    BoundsInput,
    DatasetContext,
    Dependency,
    LocationInput,
    PluginDefinition,
    RunContext,
    dataset,
    metric,
)
from lyra.sdk.errors import MetricResultError
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.job import JobCreateRequest, JobEnvelope
from lyra.sdk.models.plugin import PluginInfo, TableOutput
from shapely.geometry import Point

from lyra_app import job_store, job_submission, registry, worker
from lyra_app.config import clear_config_cache
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.plugins import InstalledPlugin
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import feature_collection

TABLE = TableOutput(columns=[])


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path) -> Iterator[None]:
    worker.set_runner_temp_base(tmp_path / "scratch")
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)
    registry.reset_catalog()
    clear_config_cache()


def install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handlers: list[Callable[..., object]],
) -> list[InstalledPlugin]:
    definitions = {
        f"plugin_{i}": PluginDefinition(nodes=[handler])
        for i, handler in enumerate(handlers)
    }
    plugins = [
        InstalledPlugin(
            name,
            definition.manifest(
                plugin=PluginInfo(name=name, version="1"), factory=f"{name}:create"
            ),
            {
                n: "heavy" if n == "root" else "lightweight"
                for n in definition.metric_names
            },
        )
        for name, definition in definitions.items()
    ]
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(
        worker, "load_plugin_definition", lambda ref: definitions[ref.split(":")[0]]
    )
    registry.initialize_catalog(config)
    worker.refresh_runner_registry("heavy", config=config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    return plugins


def native_job(monkeypatch: pytest.MonkeyPatch, root: str = "root") -> SimpleNamespace:
    entry = worker.RUNNER_REGISTRY[root]
    native = SimpleNamespace(
        id="graph-job",
        origin=entry.queue,
        worker_name="fake-worker",
        meta={
            "contract": entry.contract.model_dump(mode="json"),
            "distribution": entry.distribution,
            "provenance": {
                "plugin": entry.plugin.plugin.model_dump(mode="json"),
                "metric": entry.metric_name,
                "catalog_fingerprint": "test-catalog",
                "input": {},
                "outputs": entry.contract.outputs,
                "created_at": "2026-10-27T00:00:00Z",
            },
        },
        save_meta=Mock(),
    )
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    monkeypatch.setattr(worker.database_connection, "dispose_worker_engine", Mock())
    return native


def test_fixed_graph_routes_and_shares_context_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    contexts: list[RunContext] = []

    @dataset(name="source", description="source", outputs={"source_table": TABLE})
    def source(context: DatasetContext) -> pd.DataFrame:
        contexts.append(context)
        context.logger.info("source acquired")
        context.report_progress(stage="source", current=1)
        assert context.db is database_adapter
        return pd.DataFrame({"value": [7]}, index=[99])

    @metric(
        name="upstream",
        description="upstream",
        inputs={"data": Dependency("source_table")},
        outputs={"upstream_table": TABLE},
    )
    def upstream(data: pd.DataFrame, context: RunContext) -> pd.DataFrame:
        contexts.append(context)
        assert not hasattr(context, "db")
        return data

    @metric(
        name="root",
        description="root",
        inputs={
            "data": Dependency("upstream_table"),
            "again": Dependency("source_table"),
        },
        outputs={"answer": TABLE},
    )
    def root(
        data: pd.DataFrame, again: pd.DataFrame, context: RunContext
    ) -> pd.DataFrame:
        contexts.append(context)
        assert data is again
        return data

    database_adapter = Mock()
    monkeypatch.setattr(worker, "_build_db_context", lambda: database_adapter)
    plugins = install(tmp_path, monkeypatch, [root, upstream, source])
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert set(worker.RUNNER_REGISTRY["root"].implementations) == {
        p.distribution for p in plugins
    }
    assert registry.get_metric_entry("source") is None
    assert registry.get_metric_entry("answer") is None
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(job_store, "enqueue", enqueue)
    database = Mock(spec=ApplicationDatabaseRuntime)
    response = asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            database=database,
            job_id_factory=lambda: native.id,
        )
    )
    assert response.job_id == native.id
    database.require_spatial_engine.assert_not_called()
    enqueue.assert_called_once()
    envelope, provenance, entry = enqueue.call_args.args
    assert entry.queue == "heavy"
    assert provenance.input == {}
    assert "row_identity" not in provenance.model_dump()
    native.meta["provenance"] = provenance.model_dump(mode="json")
    with caplog.at_level(logging.INFO):
        result = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert result["outputs"] == {"answer": [{"value": 7}]}
    assert [c.node for c in contexts] == ["source", "upstream", "root"]
    assert all(c.job_id == native.id and c.temp_dir.is_dir() for c in contexts)
    assert contexts[0].temp_dir == contexts[-1].temp_dir
    assert native.meta["progress"]["stage"] == "source"
    assert "source acquired" in caplog.text
    enqueue.assert_called_once()
    worker.refresh_runner_registry("lightweight")
    assert set(worker.RUNNER_REGISTRY) == {"upstream"}
    assert worker.RUNNER_REGISTRY["upstream"].queue == "lightweight"
    enqueue.reset_mock()
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="upstream", input={}), database=database
        )
    )
    assert enqueue.call_args.args[2].queue == "lightweight"


@pytest.mark.parametrize("failure", ["dependency", "serialization"])
def test_graph_failure_is_atomic(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @dataset(name="source", description="source", outputs={"source": TABLE})
    def source() -> pd.DataFrame:
        calls.append("source")
        if failure == "dependency":
            msg = "private acquisition failure"
            raise ValueError(msg)
        return pd.DataFrame({"value": [1]})

    @metric(
        name="middle",
        description="middle",
        inputs={"data": Dependency("source")},
        outputs={"middle": TABLE},
    )
    def middle(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("middle")
        return data

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("middle")},
        outputs={"good": TABLE, "bad": TABLE},
    )
    def root(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
        calls.append("root")
        return {"good": data, "bad": gpd.GeoDataFrame(geometry=[Point(0, 0)])}

    install(tmp_path, monkeypatch, [root, middle, source])
    native = native_job(monkeypatch)
    with pytest.raises(
        GraphExecutionError if failure == "dependency" else MetricResultError
    ):
        worker.run_metric_task(
            JobEnvelope(job_id=native.id, metric="root", input={}).model_dump(
                mode="json"
            )
        )
    error = native.meta["failure"]["error"]
    assert error["dependency_path"] == (
        ["root", "middle", "source"] if failure == "dependency" else ["root"]
    )
    assert error["node"] == ("source" if failure == "dependency" else "root")
    assert error["type"] == ("worker" if failure == "dependency" else "invalid_result")
    assert calls == (
        ["source"] if failure == "dependency" else ["source", "middle", "root"]
    )
    assert "outputs" not in native.meta["failure"]
    assert isinstance(worker.database_connection.dispose_worker_engine, Mock)
    worker.database_connection.dispose_worker_engine.assert_called_once()


@pytest.mark.parametrize("mode", ["location", "bounds", "both"])
@pytest.mark.parametrize("reference", ["met_zone_code", "cvegeo_list", "geojson"])
def test_spatial_submission_resolves_once_before_multiple_consumers(
    mode: str, reference: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    def with_location(location: LocationInput) -> pd.DataFrame:
        seen.append({"location": location.model_dump(mode="json")})
        return pd.DataFrame()

    def with_bounds(bounds: BoundsInput) -> pd.DataFrame:
        seen.append({"bounds": bounds.model_dump(mode="json")})
        return pd.DataFrame()

    def with_both(location: LocationInput, bounds: BoundsInput) -> pd.DataFrame:
        seen.append(
            {
                "location": location.model_dump(mode="json"),
                "bounds": bounds.model_dump(mode="json"),
            }
        )
        return pd.DataFrame()

    def root_location(
        location: LocationInput, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is other
        return with_location(location)

    def root_bounds(
        bounds: BoundsInput, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is other
        return with_bounds(bounds)

    def root_both(
        location: LocationInput,
        bounds: BoundsInput,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert data is other
        return with_both(location, bounds)

    root_handler = {
        "location": root_location,
        "bounds": root_bounds,
        "both": root_both,
    }[mode]
    source = dataset(name="source", description="source", outputs={"source": TABLE})(
        {"location": with_location, "bounds": with_bounds, "both": with_both}[mode]
    )
    root = metric(
        name="root",
        description="root",
        inputs={"data": Dependency("source"), "other": Dependency("source")},
        outputs={"answer": TABLE},
    )(root_handler)
    install(tmp_path, monkeypatch, [root, source])
    native = native_job(monkeypatch)
    geometry = feature_collection(("request-id",))
    converters = {
        "location": Mock(return_value=GeoJSON.model_validate(geometry)),
        "bounds": Mock(return_value=SingleGeoJSON.model_validate(geometry)),
    }
    monkeypatch.setattr(
        job_submission,
        "build_converter_map",
        lambda _: {
            kind: {reference: converter} for kind, converter in converters.items()
        },
    )
    database = Mock(spec=ApplicationDatabaseRuntime)
    database.run_spatial = AsyncMock(
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)
    )
    enqueue = Mock()
    monkeypatch.setattr(job_store, "enqueue", enqueue)
    fields = ["location", "bounds"] if mode == "both" else [mode]
    value = {"met_zone_code": "09.1.01", "cvegeo_list": ["09001"], "geojson": geometry}[
        reference
    ]
    inputs = {field: {"data_type": reference, "value": value} for field in fields}
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input=inputs),
            database=database,
            job_id_factory=lambda: native.id,
        )
    )
    envelope, provenance, _ = enqueue.call_args.args
    assert provenance.input == inputs
    assert "row_identity" not in provenance.model_dump()
    native.meta["provenance"] = provenance.model_dump(mode="json")
    assert worker.run_metric_task(envelope.model_dump(mode="json"))["outputs"] == {
        "answer": []
    }
    assert len(seen) == 2
    assert seen[0] == seen[1] == envelope.input
    for field in fields:
        converters[field].assert_called_once()
    enqueue.assert_called_once()
    for name in ("source", "answer"):
        with pytest.raises(job_submission.UnknownMetricError):
            asyncio.run(
                job_submission.submit_job(
                    JobCreateRequest(metric=name, input=inputs), database=database
                )
            )
    with pytest.raises(registry.MetricPayloadValidationError):
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(metric="root", input={**inputs, "data": []}),
                database=database,
            )
        )


@pytest.mark.parametrize("issue", ["missing", "stale"])
def test_required_dependency_implementation_is_validated(
    issue: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @dataset(name="source", description="source", outputs={"source": TABLE})
    def source() -> pd.DataFrame:
        return pd.DataFrame()

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    plugins = install(tmp_path, monkeypatch, [root, source])
    if issue == "stale":
        plugins[1].manifest.nodes[0].description = "stale description"
        match = "build-manifest"
    else:
        original = worker.load_plugin_definition

        def load(ref: str) -> PluginDefinition:
            if ref.startswith("plugin_1:"):
                msg = "required source is unavailable"
                raise RuntimeError(msg)
            return original(ref)

        monkeypatch.setattr(worker, "load_plugin_definition", load)
        match = "required source"
    with pytest.raises(RuntimeError, match=match):
        worker.load_runner_metric_entries("heavy")


def test_submission_enqueues_one_native_job_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(name="root", description="root", outputs={"answer": TABLE})
    def root() -> pd.DataFrame:
        return pd.DataFrame()

    install(tmp_path, monkeypatch, [root])
    queue = Mock()
    queue_factory = Mock(return_value=queue)
    monkeypatch.setattr(job_store, "Queue", queue_factory)
    monkeypatch.setattr(job_store, "get_sync_client", Mock())
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            database=Mock(spec=ApplicationDatabaseRuntime),
            job_id_factory=lambda: "one-job",
        )
    )
    queue_factory.assert_called_once()
    assert queue_factory.call_args.args == ("heavy",)
    queue.enqueue_call.assert_called_once()
    assert queue.enqueue_call.call_args.args == ("lyra_app.worker.run_metric_task",)
    options = queue.enqueue_call.call_args.kwargs
    assert "retry" not in options
    assert options["job_id"] == "one-job"
    assert len(options["args"]) == 1
    assert options["args"][0]["metric"] == "root"
    assert options["meta"]["contract"]["name"] == "root"


@pytest.mark.parametrize(
    ("cause", "category"),
    [
        (
            worker.MetricInputError("source", "parameters.value", "invalid"),
            "invalid_input",
        ),
        (MetricResultError("source", "outputs", "invalid"), "invalid_result"),
        (
            worker.database_connection.DatabaseUnavailableError("unavailable"),
            "database_unavailable",
        ),
    ],
)
def test_graph_failure_retains_error_category(
    cause: Exception, category: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = SimpleNamespace(id="job", worker_name="worker", meta={}, save_meta=Mock())
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    error = GraphExecutionError("source", ("root", "source"), cause)
    error.__cause__ = cause
    monkeypatch.setattr(worker, "_execute_metric", Mock(side_effect=error))
    monkeypatch.setattr(worker.database_connection, "dispose_worker_engine", Mock())
    with pytest.raises(GraphExecutionError):
        worker.run_metric_task({})
    assert native.meta["failure"]["error"]["type"] == category
    assert native.meta["failure"]["error"]["node"] == "source"
    assert native.meta["failure"]["error"]["dependency_path"] == ["root", "source"]
