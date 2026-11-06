"""Exercise submission and worker graphs with local plugins and fake infrastructure."""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, cast
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from lyra.contracts.errors import MetricResultError
from lyra.contracts.jobs import JobCreateRequest, JobEnvelope
from lyra.contracts.plugins import PluginInfo
from lyra.contracts.resources import IOConfig
from lyra.data.locations import Locations
from lyra.data.results import serialize_outputs
from lyra.runtime import loading
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import compile_catalog
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import Resource
from lyra.server import job_store, job_submission, registry, worker
from lyra.server.mcp.tools import InProcessLyraBackend, ToolCallError
from lyra.server.plugins import InstalledPlugin
from lyra.server.routes.metrics import get_metric
from test_support.canonical_execution import canonical_workload
from test_support.location_helpers import node_contract
from test_support.location_resolution import Provider, selection
from test_support.worker_workloads import canonical_worker_entry
from tests.config_helpers import load_test_config
from tests.queue_helpers import accepted_envelope, run_and_read, submitted_input
from tests.resource_helpers import QueryIO, database_override
from tests.smoke_plugin_helpers import feature_collection
from tests.worker_graph_integration_helpers import TABLE, VALUES, install, native_job

if TYPE_CHECKING:
    from collections.abc import Callable

    from rq.job import Job


pytestmark = pytest.mark.usefixtures("worker_state")


def test_fixed_graph_routes_and_shares_context_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    contexts: list[RunContext] = []

    @metric(name="source", description="source", outputs={"source_table": VALUES})
    def source(
        context: RunContext, database: Annotated[QueryIO, Resource("database")]
    ) -> pd.DataFrame:
        contexts.append(context)
        context.logger.info("source acquired")
        context.report_progress(stage="source", current=1)
        assert isinstance(database, QueryIO)
        return pd.DataFrame({"value": [7]}, index=[99])

    @metric(
        name="upstream",
        description="upstream",
        inputs={"data": Dependency("source_table", producer="source")},
        outputs={"upstream_table": VALUES},
    )
    def upstream(
        data: pd.DataFrame,
        context: RunContext,
        database: Annotated[QueryIO, Resource("database")],
    ) -> pd.DataFrame:
        contexts.append(context)
        assert isinstance(database, QueryIO)
        return data

    @metric(
        name="root",
        description="root",
        inputs={
            "data": Dependency("upstream_table", producer="upstream"),
            "again": Dependency("source_table", producer="source"),
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
    resource = database_override(database_adapter)
    plugins, runtime = install(
        tmp_path, monkeypatch, [root, upstream, source], {"database": resource}
    )
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert set(worker.RUNNER_REGISTRY["root"].implementations) == {
        p.distribution for p in plugins
    }
    assert "source" in runtime.catalog.get_loaded_metric_names()
    assert "answer" not in runtime.catalog.get_loaded_metric_names()
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    response = asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    assert response.job_id == native.id
    enqueue.assert_called_once()
    (envelope,) = enqueue.call_args.args
    provenance = envelope.accepted
    assert enqueue.call_args.kwargs["queue"] == "heavy"
    assert submitted_input(envelope) == {}
    assert "row_identity" not in provenance.model_dump()
    native.args = (envelope.model_dump(mode="json"),)
    with caplog.at_level(logging.INFO):
        result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert result["outputs"] == {"answer": [{"value": 7}]}
    assert [c.node for c in contexts] == ["source", "upstream", "root"]
    assert all(c.job_id == native.id and c.temp_dir.is_dir() for c in contexts)
    assert contexts[0].temp_dir == contexts[-1].temp_dir
    assert native.meta["progress"]["stage"] == "source"
    assert "source acquired" in caplog.text
    enqueue.assert_called_once()
    worker.refresh_runner_registry("lightweight", config=runtime.config)
    assert set(worker.RUNNER_REGISTRY) == {"source", "upstream"}
    assert worker.RUNNER_REGISTRY["upstream"].queue == "lightweight"
    enqueue.reset_mock()
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="upstream", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
        )
    )
    assert enqueue.call_args.kwargs["queue"] == "lightweight"


def test_source_discovery_submission_and_database_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_adapter = Mock()
    database_adapter.query_dataframe.return_value = pd.DataFrame({"value": [7]})
    resource = database_override(database_adapter)

    @metric(name="source", description="source", outputs={"source_table": VALUES})
    def source(database: Annotated[QueryIO, Resource("database")]) -> pd.DataFrame:
        return database.query_dataframe("SELECT value FROM source")

    _, runtime = install(tmp_path, monkeypatch, [source], {"database": resource})
    worker.refresh_runner_registry("lightweight", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    assert asyncio.run(get_metric("source", runtime.catalog)).family.name == "source"
    backend = InProcessLyraBackend(lambda: runtime)
    info = asyncio.run(backend.get_discovery()).inspect("source")
    assert info is not None
    assert info.family.name == "source"
    native = native_job(monkeypatch, "source")
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="source", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    (envelope,) = enqueue.call_args.args
    assert enqueue.call_args.kwargs["queue"] == "lightweight"
    native.args = (envelope.model_dump(mode="json"),)
    result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
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
        inputs={"data": Dependency("source", producer="source")},
        outputs={"middle": VALUES},
    )
    def middle(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("middle")
        return data

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("middle", producer="middle")},
        outputs={"good": VALUES, "bad": VALUES},
    )
    def root(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
        calls.append("root")
        return {"good": data, "bad": gpd.GeoDataFrame(geometry=[Point(0, 0)])}

    install(tmp_path, monkeypatch, [root, middle, source])
    native = native_job(monkeypatch)
    with pytest.raises(GraphExecutionError):
        run_and_read(
            worker.run_metric_task,
            accepted_envelope(job_id=native.id, metric="root", inputs={}).model_dump(
                mode="json"
            ),
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


@pytest.mark.parametrize("mode", ["location", "bounds", "both"])
def test_worker_resolves_once_before_multiple_consumers(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    def with_location(location: Locations) -> pd.DataFrame:
        seen.append({"location": location.to_dict()})
        return pd.DataFrame()

    def with_bounds(bounds: Locations) -> pd.DataFrame:
        seen.append({"bounds": bounds.to_dict()})
        return pd.DataFrame()

    def with_both(location: Locations, bounds: Locations) -> pd.DataFrame:
        seen.append(
            {
                "location": location.to_dict(),
                "bounds": bounds.to_dict(),
            }
        )
        return pd.DataFrame()

    def root_location(
        location: Locations, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is other
        return with_location(location)

    def root_bounds(
        bounds: Locations, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is other
        return with_bounds(bounds)

    def root_both(
        location: Locations,
        bounds: Locations,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert data is other
        return with_both(location, bounds)

    source = metric(
        locations={
            key: node_contract()
            for key in (("bounds", "location") if mode == "both" else (mode,))
        },
        name="source",
        description="source",
        outputs={"source": TABLE},
    )({"location": with_location, "bounds": with_bounds, "both": with_both}[mode])
    root = metric(
        locations={
            key: node_contract()
            for key in (("bounds", "location") if mode == "both" else (mode,))
        },
        name="root",
        description="root",
        inputs={
            "data": Dependency("source", producer="source"),
            "other": Dependency("source", producer="source"),
        },
        outputs={"answer": TABLE},
    )({"location": root_location, "bounds": root_bounds, "both": root_both}[mode])
    runtime = install(tmp_path, monkeypatch, [root, source])[1]
    native = native_job(monkeypatch)

    provider = PluginDefinition(
        metrics=[], resources={"locations": Provider}, location_provider="locations"
    )
    manifest = provider.manifest(
        plugin=PluginInfo(name="provider", version="1"), factory="provider:create"
    )
    runtime.catalog = registry.Catalog(
        compile_catalog({**runtime.catalog.compiled.manifests, "provider": manifest}),
        runtime.catalog.metric_queues,
    )
    entry = worker.RUNNER_REGISTRY["root"]
    entry.implementations["provider"] = BoundPlugin(provider)

    worker.RUNNER_REGISTRY["root"] = replace(entry, catalog=runtime.catalog.compiled)
    resolve = Mock(wraps=Provider(config=IOConfig(), secrets={}).resolve)
    monkeypatch.setattr(Provider, "resolve", resolve)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    fields = ["location", "bounds"] if mode == "both" else [mode]
    inputs = {
        field: selection("request-id").model_dump(mode="json") for field in fields
    }
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    (envelope,) = enqueue.call_args.args
    provenance = envelope.accepted
    assert submitted_input(envelope) == inputs
    assert "row_identity" not in provenance.model_dump()
    native.args = (envelope.model_dump(mode="json"),)
    assert run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))[
        "outputs"
    ] == {"answer": []}
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert submitted_input(envelope) == inputs
    assert all("features" in value for value in seen[0].values())
    assert resolve.call_count == len(fields)
    enqueue.assert_called_once()
    for name in ("answer",):
        with pytest.raises(job_submission.UnknownMetricError):
            asyncio.run(
                job_submission.submit_job(
                    JobCreateRequest(metric=name, input=inputs),
                    catalog=runtime.catalog,
                    jobs=runtime.jobs,
                )
            )
    with pytest.raises(job_submission.MetricPayloadValidationError):
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(metric="root", input={**inputs, "data": []}),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
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
        inputs={"data": Dependency("source", producer="source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    plugins, runtime = install(tmp_path, monkeypatch, [root, source])
    if issue == "stale":
        plugins[1].manifest.nodes[0].description = "stale description"
        match = "build-manifest"
    else:
        original = loading.load_plugin_definition

        def load(ref: str) -> PluginDefinition:
            if ref.startswith("plugin_1:"):
                msg = "required source is unavailable"
                raise RuntimeError(msg)
            return original(ref)

        monkeypatch.setattr(loading, "load_plugin_definition", load)
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
            job_id_factory=lambda: "one-job",
        )
    )
    queue_factory.assert_called_once()
    assert queue_factory.call_args.args == ("heavy",)
    queue.enqueue_call.assert_called_once()
    assert queue.enqueue_call.call_args.args == ("lyra.server.worker.run_metric_task",)
    options = queue.enqueue_call.call_args.kwargs
    assert "retry" not in options
    assert options["job_id"] == "one-job"
    assert len(options["args"]) == 1
    assert options["args"][0]["accepted"]["plan"]["node"] == "root"
    assert options["meta"] == {}
    native = native_job(monkeypatch)
    native.id = options["job_id"]
    native.meta.update(options["meta"])
    result = run_and_read(worker.execute_job, options["args"][0], cast("Job", native))
    assert result["job_id"] == "one-job"
    assert result["outputs"] == {"answer": []}
    assert "provenance" not in result
    assert native.meta["execution"]["succeeded"]


@pytest.mark.parametrize(
    ("cause", "category"),
    [
        (
            worker.MetricInputError("source", "parameters.value", "invalid"),
            "invalid_input",
        ),
        (MetricResultError("source", "outputs", "invalid"), "invalid_result"),
        (RuntimeError("private query failure"), "worker"),
    ],
)
def test_graph_failure_retains_error_category(
    cause: Exception, category: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = SimpleNamespace(id="job", worker_name="worker", meta={}, save_meta=Mock())
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    error = GraphExecutionError("source", ("root", "source"), cause)
    error.__cause__ = cause
    monkeypatch.setattr(worker, "execute_job", Mock(side_effect=error))
    with pytest.raises(GraphExecutionError):
        run_and_read(worker.run_metric_task, {})
    assert native.meta["failure"]["error"]["type"] == category
    assert native.meta["failure"]["error"]["node"] == "source"
    assert native.meta["failure"]["error"]["dependency_path"] == ["root", "source"]


@pytest.mark.parametrize("reference", ["geojson", "cvegeo_list", "met_zone_code"])
def test_database_free_spatial_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    @metric(
        locations={"location": node_contract()},
        name="root",
        description="spatial",
        outputs={"answer": TABLE},
    )
    def root(location: Locations) -> pd.DataFrame:
        return pd.DataFrame({"count": [len(location)]})

    runtime = install(tmp_path, monkeypatch, [root])[1]
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
    with pytest.raises(ToolCallError) as error:
        asyncio.run(backend.create_job(JobCreateRequest(metric="root", input=payload)))
    assert error.value.code == "invalid_parameters"
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
        inputs={"data": Dependency("source", producer="source")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return data

    _, runtime = install(tmp_path, monkeypatch, [root, source])
    runtime.config.artifact_dir = tmp_path / "tables"
    for number, (pool, name) in enumerate(
        [
            ("lightweight", "source"),
            ("heavy", "root"),
            ("heavy", "root"),
        ]
    ):
        worker.refresh_runner_registry(pool, config=runtime.config)
        native = native_job(monkeypatch, name)
        native.id = f"cached-job-{number}"
        result = run_and_read(
            worker.run_metric_task,
            accepted_envelope(job_id=native.id, metric=name, inputs={}).model_dump(
                mode="json"
            ),
        )
        assert result["job_id"] == native.id
    assert calls == ["source", "root"]


@pytest.mark.parametrize("revision", [None, " data-1 "])
def test_revision_submission_to_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str | None
) -> None:
    calls = []

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame({"value": [7]})

    _, runtime = install(tmp_path, monkeypatch, [root])
    runtime.config.artifact_dir = tmp_path / "tables"
    worker.refresh_runner_registry("heavy", config=runtime.config)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    for revision_value, count in [
        (revision, 1),
        (revision, 1),
        ("changed", 2),
        (revision, 2),
    ]:
        native = native_job(monkeypatch, "root")
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(
                    metric="root", input={}, cache_revision=revision_value
                ),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
                job_id_factory=lambda native=native: native.id,
            )
        )
        (envelope,) = enqueue.call_args.args
        native.args = (envelope.model_dump(mode="json"),)
        payload = JobEnvelope.model_validate_json(
            envelope.model_dump_json()
        ).model_dump(mode="json")
        assert (
            payload["accepted"]["plan"]["request"]["cache_revision"] == revision_value
        )
        run_and_read(worker.run_metric_task, payload)
        assert len(calls) == count


def test_file_graph_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @metric(name="file_source", description="file source", outputs={"data": VALUES})
    def file_source(context: RunContext) -> pd.DataFrame:
        path = context.temp_dir / "values.csv"
        path.write_text("value\n7\n")
        return pd.read_csv(path)

    @metric(
        name="root",
        description="file metric",
        inputs={"data": Dependency("data", producer="file_source")},
        outputs={"answer": VALUES},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    handlers: list[Callable[..., object]] = [root, file_source]
    runtime = install(tmp_path, monkeypatch, handlers)[1]
    worker.refresh_runner_registry("heavy", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    (envelope,) = enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    assert run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))[
        "outputs"
    ] == {"answer": [{"value": 7}]}


@pytest.mark.parametrize("change", [None, "contract", "missing", "failure"])
def test_serialized_worker_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str | None
) -> None:

    calls = []

    @metric(
        name="root",
        family="report",
        edition="annual",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root() -> pd.DataFrame:
        calls.append("root")
        if change == "failure":
            raise RuntimeError
        return pd.DataFrame({"value": [7]})

    _, runtime = install(tmp_path, monkeypatch, [root])
    runtime.config.artifact_dir = tmp_path / "cache"
    worker.refresh_runner_registry("heavy", config=runtime.config)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="report", edition="annual", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: "graph-job",
        )
    )
    (envelope,) = enqueue.call_args.args
    provenance = envelope.accepted
    payload = envelope.accepted.plan
    assert payload == provenance.plan
    assert envelope.accepted.plan.edition == provenance.plan.edition == "annual"
    native = native_job(monkeypatch)
    native.args = (envelope.model_dump(mode="json"),)
    if change == "contract":
        entry = worker.RUNNER_REGISTRY["root"]
        assert entry.contract.kind == "metric"
        entry.contract.outputs["answer"].meaning = "changed"
        worker.RUNNER_REGISTRY["root"] = replace(
            entry, catalog=compile_catalog(entry.catalog.manifests)
        )
    elif change == "missing":
        worker.RUNNER_REGISTRY["root"].implementations.clear()
    if change is not None:
        with pytest.raises((ValueError, GraphExecutionError)):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert "failure" in native.meta
        assert native.args[0]["accepted"]["plan"] == payload.model_dump(mode="json")
        assert calls == (["root"] if change == "failure" else [])
        return
    first = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    second = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert first["outputs"] == second["outputs"] == {"answer": [{"value": 7}]}
    assert calls == ["root"]
    assert envelope.accepted.plan.node == "root"
    assert native.meta["execution"]["observations"][0]["outcome"] == "cache_hit"


@pytest.mark.parametrize("changed", [False, True])
def test_worker_captured_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, changed: bool
) -> None:
    work = canonical_workload("routed", 2, 2)
    expected = work.executor.execute(work.prepared).outputs
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    entry = canonical_worker_entry(work)
    monkeypatch.setitem(worker.RUNNER_REGISTRY, "two", entry)
    native = native_job(monkeypatch, "two")
    installed = InstalledPlugin(
        "bench",
        entry.plugin,
        dict.fromkeys(entry.implementations["bench"].definition.metric_names, "heavy"),
    )
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: [installed])
    jobs = Mock(spec=job_store.JobStore)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(
                metric="two",
                input={
                    item.name: {
                        "collection": item.binding.source.collection,
                        "unit_type": item.binding.source.unit_type,
                        "ids": list(item.ids),
                    }
                    for item in work.prepared.inputs
                },
            ),
            catalog=registry.Catalog.load(config.plugins),
            jobs=jobs,
            job_id_factory=lambda: native.id,
        )
    )
    (envelope,) = jobs.enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    if changed:
        operation = entry.graph.nodes["select"].node
        assert operation.kind == "location_operation"
        assert operation.output_contract is not None
        assert operation.kind == "location_operation"
        reference = operation.output_contract.reference
        for node in entry.graph.nodes.values():
            for contract in (
                *(item.contract for item in node.node.location_inputs.values()),
                *(
                    (node.node.output_contract,)
                    if node.node.kind == "location_operation"
                    else ()
                ),
            ):
                if contract.reference == reference:
                    contract.data_revision = "new"
        assert entry.location_provider is not None
        for contract in entry.location_provider.declaration.contracts:
            if contract.reference == reference:
                contract.data_revision = "new"
        worker.RUNNER_REGISTRY["two"] = replace(
            entry, catalog=compile_catalog(entry.catalog.manifests)
        )
        with pytest.raises((ValueError, GraphExecutionError)):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert "failure" in native.meta
    else:
        result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert result["outputs"] == serialize_outputs("two", expected)
        native.meta["execution"]
        assert envelope.model_dump(mode="json")["accepted"]["plan"]["inputs"][0][
            "binding"
        ]["operations"] == ["select"]
