"""Exercise submission and worker graphs with local plugins and fake infrastructure."""

import asyncio
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock, Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    BoundsInput,
    DatabaseIO,
    Dependency,
    LocationInput,
    PluginDefinition,
    Resource,
    RunContext,
    metric,
    plugin_loader,
)
from lyra.sdk.errors import MetricResultError
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.job import JobCreateRequest, JobEnvelope
from lyra.sdk.models.plugin import PluginInfo
from shapely.geometry import Point

from lyra_app import job_store, job_submission, registry, worker
from lyra_app.db.connection import (
    ApplicationDatabaseRuntime,
    DatabaseNotConfiguredError,
)
from lyra_app.mcp.tools import InProcessLyraBackend, ToolCallError
from lyra_app.plugins import InstalledPlugin
from lyra_app.routes.metrics import get_metric
from lyra_app.runtime import ApplicationRuntime
from tests.config_helpers import load_test_config, worker_credentials
from tests.resource_helpers import database_override
from tests.runtime_helpers import make_runtime
from tests.smoke_plugin_helpers import feature_collection

TABLE = pa.DataFrameSchema({})
VALUES = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path) -> Iterator[None]:
    worker.set_runner_temp_base(tmp_path / "scratch")
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handlers: list[Callable[..., object]],
) -> tuple[list[InstalledPlugin], ApplicationRuntime]:
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
        plugin_loader,
        "load_plugin_definition",
        lambda ref: definitions[ref.split(":")[0]],
    )
    runtime = make_runtime(config)
    worker.refresh_runner_registry(
        "heavy", config=config, credentials=worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    return plugins, runtime


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

    @metric(name="source", description="source", outputs={"source_table": VALUES})
    def source(
        context: RunContext, database: Annotated[DatabaseIO, Resource("database")]
    ) -> pd.DataFrame:
        contexts.append(context)
        context.logger.info("source acquired")
        context.report_progress(stage="source", current=1)
        assert isinstance(database, DatabaseIO)
        return pd.DataFrame({"value": [7]}, index=[99])

    @metric(
        name="upstream",
        description="upstream",
        inputs={"data": Dependency("source_table")},
        outputs={"upstream_table": VALUES},
    )
    def upstream(
        data: pd.DataFrame,
        context: RunContext,
        database: Annotated[DatabaseIO, Resource("database")],
    ) -> pd.DataFrame:
        contexts.append(context)
        assert isinstance(database, DatabaseIO)
        return data

    @metric(
        name="root",
        description="root",
        inputs={
            "data": Dependency("upstream_table"),
            "again": Dependency("source_table"),
        },
        outputs={"answer": VALUES},
    )
    def root(
        data: pd.DataFrame, again: pd.DataFrame, context: RunContext
    ) -> pd.DataFrame:
        contexts.append(context)
        pd.testing.assert_frame_equal(data, again)
        return data

    database_adapter = Mock()
    monkeypatch.setattr(worker, "WorkerDatabaseIO", database_override(database_adapter))
    plugins, runtime = install(tmp_path, monkeypatch, [root, upstream, source])
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert set(worker.RUNNER_REGISTRY["root"].implementations) == {
        p.distribution for p in plugins
    }
    assert runtime.catalog.get_metric_entry("source") is not None
    assert runtime.catalog.get_metric_entry("answer") is None
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    database = Mock(spec=ApplicationDatabaseRuntime)
    response = asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
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
    worker.refresh_runner_registry(
        "lightweight", config=runtime.config, credentials=worker_credentials()
    )
    assert set(worker.RUNNER_REGISTRY) == {"source", "upstream"}
    assert worker.RUNNER_REGISTRY["upstream"].queue == "lightweight"
    enqueue.reset_mock()
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="upstream", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            database=database,
        )
    )
    assert enqueue.call_args.args[2].queue == "lightweight"


def test_source_discovery_submission_and_database_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_adapter = Mock()
    database_adapter.query_dataframe.return_value = pd.DataFrame({"value": [7]})
    monkeypatch.setattr(worker, "WorkerDatabaseIO", database_override(database_adapter))

    @metric(name="source", description="source", outputs={"source_table": VALUES})
    def source(database: Annotated[DatabaseIO, Resource("database")]) -> pd.DataFrame:
        return database.query_dataframe("SELECT value FROM source")

    _, runtime = install(tmp_path, monkeypatch, [source])
    worker.refresh_runner_registry(
        "lightweight", config=runtime.config, credentials=worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    assert asyncio.run(get_metric("source", runtime.catalog)).name == "source"
    backend = InProcessLyraBackend(lambda: runtime)
    info = asyncio.run(backend.get_metric("source"))
    assert info is not None
    assert info.name == "source"
    native = native_job(monkeypatch, "source")
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    database = Mock(spec=ApplicationDatabaseRuntime)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="source", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            database=database,
            job_id_factory=lambda: native.id,
        )
    )
    envelope, provenance, entry = enqueue.call_args.args
    assert entry.queue == "lightweight"
    native.meta["provenance"] = provenance.model_dump(mode="json")
    result = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert result["outputs"] == {"source_table": [{"value": 7}]}


@pytest.mark.parametrize("failure", ["dependency", "serialization"])
def test_graph_failure_is_atomic(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(name="source", description="source", outputs={"source": VALUES})
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
        outputs={"middle": VALUES},
    )
    def middle(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("middle")
        return data

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("middle")},
        outputs={"good": VALUES, "bad": VALUES},
    )
    def root(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
        calls.append("root")
        return {"good": data, "bad": gpd.GeoDataFrame(geometry=[Point(0, 0)])}

    install(tmp_path, monkeypatch, [root, middle, source])
    native = native_job(monkeypatch)
    with pytest.raises(GraphExecutionError):
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
    worker.database_connection.dispose_worker_engine.assert_not_called()


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

    source = metric(name="source", description="source", outputs={"source": TABLE})(
        {"location": with_location, "bounds": with_bounds, "both": with_both}[mode]
    )
    root = metric(
        name="root",
        description="root",
        inputs={"data": Dependency("source"), "other": Dependency("source")},
        outputs={"answer": TABLE},
    )({"location": root_location, "bounds": root_bounds, "both": root_both}[mode])
    runtime = install(tmp_path, monkeypatch, [root, source])[1]
    native = native_job(monkeypatch)
    geometry = feature_collection(("request-id",))
    converters = {
        "location": Mock(return_value=GeoJSON.model_validate(geometry)),
        "bounds": Mock(return_value=SingleGeoJSON.model_validate(geometry)),
    }
    patch_converters(monkeypatch, reference, converters)
    database = Mock(spec=ApplicationDatabaseRuntime)
    database.run_spatial = AsyncMock(
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)
    )
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    fields = ["location", "bounds"] if mode == "both" else [mode]
    value = {"met_zone_code": "09.1.01", "cvegeo_list": ["09001"], "geojson": geometry}[
        reference
    ]
    inputs = {field: {"data_type": reference, "value": value} for field in fields}
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
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
        assert converters[field].call_count == int(reference != "geojson")
    enqueue.assert_called_once()
    for name in ("answer",):
        with pytest.raises(job_submission.UnknownMetricError):
            asyncio.run(
                job_submission.submit_job(
                    JobCreateRequest(metric=name, input=inputs),
                    catalog=runtime.catalog,
                    jobs=runtime.jobs,
                    database=database,
                )
            )
    with pytest.raises(registry.MetricPayloadValidationError):
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(metric="root", input={**inputs, "data": []}),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
                database=database,
            )
        )


@pytest.mark.parametrize("issue", ["missing", "stale"])
def test_required_dependency_implementation_is_validated(
    issue: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(name="source", description="source", outputs={"source": TABLE})
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

    plugins, runtime = install(tmp_path, monkeypatch, [root, source])
    if issue == "stale":
        plugins[1].manifest.nodes[0].description = "stale description"
        match = "build-manifest"
    else:
        original = plugin_loader.load_plugin_definition

        def load(ref: str) -> PluginDefinition:
            if ref.startswith("plugin_1:"):
                msg = "required source is unavailable"
                raise RuntimeError(msg)
            return original(ref)

        monkeypatch.setattr(plugin_loader, "load_plugin_definition", load)
        match = "required source"
    with pytest.raises(RuntimeError, match=match):
        worker.load_runner_metric_entries("heavy", config=runtime.config)


def test_submission_enqueues_one_native_job_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(name="root", description="root", outputs={"answer": TABLE})
    def root() -> pd.DataFrame:
        return pd.DataFrame()

    _plugins, runtime = install(tmp_path, monkeypatch, [root])
    queue = Mock()
    queue_factory = Mock(return_value=queue)
    monkeypatch.setattr(job_store, "Queue", queue_factory)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
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
        (DatabaseNotConfiguredError(), "database_not_configured"),
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


def patch_converters(
    monkeypatch: pytest.MonkeyPatch, reference: str, converters: dict[str, Mock]
) -> None:
    if reference != "geojson":
        converter_name = (
            "load_from_cvegeos"
            if reference == "cvegeo_list"
            else "load_from_met_zone_code"
        )
        for kind, converter in converters.items():
            monkeypatch.setattr(
                f"lyra_app.converters.{kind}.{converter_name}", converter
            )


@pytest.mark.parametrize("uses_database", [False, True])
def test_database_free_graph_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, uses_database: bool
) -> None:
    @metric(name="source", description="file source", outputs={"data": VALUES})
    def source(
        context: RunContext, database: Annotated[DatabaseIO, Resource("database")]
    ) -> pd.DataFrame:
        context.report_progress(stage="read", current=1)
        path = context.temp_dir / "values.csv"
        path.write_text("value\n7\n")
        if uses_database:
            database.query_dataframe("SELECT 1")
        return pd.read_csv(path)

    @metric(
        name="root",
        description="file metric",
        inputs={"data": Dependency("data")},
        outputs={"answer": VALUES},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    handlers: list[Callable[..., object]] = [root, source]
    if not uses_database:

        @metric(name="file_source", description="file source", outputs={"data": VALUES})
        def file_source(context: RunContext) -> pd.DataFrame:
            path = context.temp_dir / "values.csv"
            path.write_text("value\n7\n")
            return pd.read_csv(path)

        handlers = [root, file_source]
    runtime = install(tmp_path, monkeypatch, handlers)[1]
    runtime.config.database = None
    worker.refresh_runner_registry(
        "heavy", config=runtime.config, credentials=worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            database=None,
            job_id_factory=lambda: native.id,
        )
    )
    envelope, provenance, _ = enqueue.call_args.args
    native.meta["provenance"] = provenance.model_dump(mode="json")
    if uses_database:
        with pytest.raises(GraphExecutionError):
            worker.run_metric_task(envelope.model_dump(mode="json"))
        failure = native.meta["failure"]["error"]
        assert failure["type"] == "database_not_configured"
        assert failure["retryable"] is False
        assert failure["dependency_path"] == ["root", "source"]
    else:
        assert worker.run_metric_task(envelope.model_dump(mode="json"))["outputs"] == {
            "answer": [{"value": 7}]
        }


@pytest.mark.parametrize("reference", ["geojson", "cvegeo_list", "met_zone_code"])
def test_database_free_spatial_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    @metric(name="root", description="spatial", outputs={"answer": TABLE})
    def root(location: LocationInput) -> pd.DataFrame:
        return pd.DataFrame({"count": [len(location.features)]})

    runtime = install(tmp_path, monkeypatch, [root])[1]
    runtime.database = None
    runtime.config.database = None
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    value = {
        "geojson": feature_collection(),
        "cvegeo_list": ["09001"],
        "met_zone_code": "09.01",
    }[reference]
    payload = JobCreateRequest(
        metric="root", input={"location": {"data_type": reference, "value": value}}
    ).input
    backend = InProcessLyraBackend(lambda: runtime)
    if reference == "geojson":
        asyncio.run(backend.create_job(JobCreateRequest(metric="root", input=payload)))
        envelope, provenance, _ = enqueue.call_args.args
        assert envelope.input == {"location": value}
        assert provenance.input == payload
    else:
        with pytest.raises(ToolCallError) as error:
            asyncio.run(
                backend.create_job(JobCreateRequest(metric="root", input=payload))
            )
        assert error.value.code == "database_not_configured"
        assert error.value.details == {"retryable": False}
        enqueue.assert_not_called()


def test_worker_jobs_share_persistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    @metric(name="source", description="Source", outputs={"source": VALUES})
    def source() -> pd.DataFrame:
        calls.append("source")
        return pd.DataFrame({"value": [7]})

    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={"data": Dependency("source")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return data

    _, runtime = install(tmp_path, monkeypatch, [root, source])
    runtime.config.cache_dir = tmp_path / "tables"
    for number, (pool, name) in enumerate(
        [
            ("lightweight", "source"),
            ("heavy", "root"),
            ("heavy", "root"),
        ]
    ):
        worker.refresh_runner_registry(
            pool, config=runtime.config, credentials=worker_credentials()
        )
        native = native_job(monkeypatch, name)
        native.id = f"cached-job-{number}"
        result = worker.run_metric_task(
            JobEnvelope(job_id=native.id, metric=name, input={}).model_dump(mode="json")
        )
        assert result["job_id"] == native.id
    assert calls == ["source", "root"]
