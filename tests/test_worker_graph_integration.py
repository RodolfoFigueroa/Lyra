"""Exercise submission and worker graphs with local plugins and fake infrastructure."""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, cast
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from lyra.contracts.errors import MetricResultError
from lyra.contracts.jobs import JobCreateRequest, JobEnvelope
from lyra.contracts.references import new_job_id
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import metric
from lyra.sdk.resources import Resource
from lyra.server import job_store, job_submission, registry, worker
from lyra.server.mcp.tools import InProcessLyraBackend, ToolCallError
from lyra.server.plugins import InstalledPlugin
from lyra.server.routes.metrics import get_metric
from test_support.canonical_execution import canonical_workload
from test_support.location_resolution import Provider, selection
from test_support.table_fixtures import value_frame
from tests.config_helpers import load_test_config
from tests.job_id_helpers import TEST_JOB_ID
from tests.provider_execution_helpers import provider_execution_definitions
from tests.queue_helpers import (
    accepted_envelope,
    read_retained,
    run_and_read,
    submitted_input,
)
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

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="source",
        outputs={"source_table": VALUES},
    )
    def source(
        context: RunContext, database: Annotated[QueryIO, Resource("database")]
    ) -> pd.DataFrame:
        contexts.append(context)
        context.logger.info("source acquired")
        context.report_progress(stage="source", current=1)
        assert isinstance(database, QueryIO)
        return pd.DataFrame({"value": [7]}, index=[99])

    @metric(
        cache="full",
        code_revision="1",
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
        cache="full",
        code_revision="1",
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
    _, runtime = install(
        tmp_path, monkeypatch, [root, upstream, source], {"database": resource}
    )
    assert "source" in runtime.catalog.get_loaded_metric_names()
    assert "answer" not in runtime.catalog.get_loaded_metric_names()
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    response = asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    assert response.job_id == native.id
    enqueue.assert_called_once()
    (envelope,) = enqueue.call_args.args
    provenance = envelope.accepted
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
    enqueue.reset_mock()
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="upstream", inputs={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
        )
    )
    assert enqueue.call_args.args[0].accepted.plan.metric == "upstream"


def test_source_discovery_submission_and_database_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_adapter = Mock()
    database_adapter.query_dataframe.return_value = pd.DataFrame({"value": [7]})
    resource = database_override(database_adapter)

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="source",
        outputs={"source_table": VALUES},
    )
    def source(database: Annotated[QueryIO, Resource("database")]) -> pd.DataFrame:
        return database.query_dataframe("SELECT value FROM source")

    _, runtime = install(tmp_path, monkeypatch, [source], {"database": resource})
    worker.initialize_worker(config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    assert asyncio.run(get_metric("source", runtime.catalog)).metric.name == "source"
    backend = InProcessLyraBackend(lambda: runtime)
    info = asyncio.run(backend.get_discovery()).inspect("source")
    assert info is not None
    assert info.metric.name == "source"
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="source", inputs={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    (envelope,) = enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert result["outputs"] == {"source_table": [{"value": 7}]}


@pytest.mark.parametrize("failure", ["dependency", "serialization"])
def test_graph_failure_is_atomic(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="source",
        outputs={"source": VALUES},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        if failure == "dependency":
            msg = "private acquisition failure"
            raise ValueError(msg)
        return pd.DataFrame({"value": [1]})

    @metric(
        cache="full",
        code_revision="1",
        name="middle",
        description="middle",
        inputs={"data": Dependency("source", producer="source")},
        outputs={"middle": VALUES},
    )
    def middle(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("middle")
        return data

    @metric(
        cache="full",
        code_revision="1",
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

    def with_location(location: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append({"location": location.to_dict()})
        return value_frame()

    def with_bounds(bounds: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append({"bounds": bounds.to_dict()})
        return value_frame()

    def with_both(location: gpd.GeoDataFrame, bounds: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(
            {
                "location": location.to_dict(),
                "bounds": bounds.to_dict(),
            }
        )
        return value_frame()

    def root_location(
        location: gpd.GeoDataFrame, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is not other
        pd.testing.assert_frame_equal(data, other)
        return with_location(location)

    def root_bounds(
        bounds: gpd.GeoDataFrame, data: pd.DataFrame, other: pd.DataFrame
    ) -> pd.DataFrame:
        assert data is not other
        pd.testing.assert_frame_equal(data, other)
        return with_bounds(bounds)

    def root_both(
        location: gpd.GeoDataFrame,
        bounds: gpd.GeoDataFrame,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert data is not other
        pd.testing.assert_frame_equal(data, other)
        return with_both(location, bounds)

    source = metric(
        inputs={
            key: LocationDependency(provider=Provider.id)
            for key in (("bounds", "location") if mode == "both" else (mode,))
        },
        code_revision="1",
        name="source",
        description="source",
        outputs={"source": TABLE},
    )({"location": with_location, "bounds": with_bounds, "both": with_both}[mode])
    root = metric(
        code_revision="1",
        name="root",
        description="root",
        inputs={
            **{
                key: LocationDependency(provider=Provider.id)
                for key in (("bounds", "location") if mode == "both" else (mode,))
            },
            "data": Dependency("source", producer="source"),
            "other": Dependency("source", producer="source"),
        },
        outputs={"answer": TABLE},
    )({"location": root_location, "bounds": root_bounds, "both": root_both}[mode])
    runtime = install(tmp_path, monkeypatch, [root, source], {"locations": Provider})[1]
    native = native_job(monkeypatch)

    resolve = create_autospec(Provider.resolve, side_effect=Provider.resolve)
    monkeypatch.setattr(Provider, "resolve", resolve)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    fields = ["location", "bounds"] if mode == "both" else [mode]
    inputs = {
        field: selection("request-id").model_dump(mode="json") for field in fields
    }
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest.model_validate(
                {
                    "metric": "root",
                    "inputs": {k: v for k, v in inputs.items() if k != "parameters"},
                    **(
                        {"parameters": inputs["parameters"]}
                        if "parameters" in inputs
                        else {}
                    ),
                }
            ),
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
    assert all(
        {"location_id", "geometry", "original"} == set(value)
        for value in seen[0].values()
    )
    assert resolve.call_count == 1
    enqueue.assert_called_once()
    for name in ("answer",):
        with pytest.raises(job_submission.UnknownMetricError):
            asyncio.run(
                job_submission.submit_job(
                    JobCreateRequest.model_validate(
                        {
                            "metric": name,
                            "inputs": {
                                k: v for k, v in inputs.items() if k != "parameters"
                            },
                            **(
                                {"parameters": inputs["parameters"]}
                                if "parameters" in inputs
                                else {}
                            ),
                        }
                    ),
                    catalog=runtime.catalog,
                    jobs=runtime.jobs,
                )
            )
    with pytest.raises(job_submission.MetricPayloadValidationError):
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(
                    metric="root", inputs={**inputs, "data": []}, parameters={}
                ),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
            )
        )


def test_submission_enqueues_one_native_job_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="root",
        outputs={"answer": TABLE},
    )
    def root() -> pd.DataFrame:
        return value_frame()

    _plugins, runtime = install(tmp_path, monkeypatch, [root])
    queue = Mock()
    queue_factory = Mock(return_value=queue)
    monkeypatch.setattr(job_store, "Queue", queue_factory)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: "e" * 32,
        )
    )
    queue_factory.assert_called_once()
    assert queue_factory.call_args.args == (runtime.config.jobs.queue,)
    queue.enqueue_call.assert_called_once()
    assert queue.enqueue_call.call_args.args == ("lyra.server.worker.run_metric_task",)
    options = queue.enqueue_call.call_args.kwargs
    assert "retry" not in options
    assert options["job_id"] == "e" * 32
    assert len(options["args"]) == 1
    assert options["args"][0]["accepted"]["plan"]["request"]["metric"] == "root"
    assert options["meta"] == {}
    native = native_job(monkeypatch)
    native.id = options["job_id"]
    native.meta.update(options["meta"])
    result = run_and_read(worker.execute_job, options["args"][0], cast("Job", native))
    assert result["job_id"] == "e" * 32
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
    native = SimpleNamespace(
        id=TEST_JOB_ID,
        worker_name="worker",
        meta={},
        save_meta=Mock(),
    )
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
        cache="full",
        code_revision="1",
        inputs={"location": LocationDependency(provider=Provider.id)},
        name="root",
        description="spatial",
        outputs={"answer": TABLE},
    )
    def root(location: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"count": [len(location)]})

    runtime = install(tmp_path, monkeypatch, [root], {"locations": Provider})[1]
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    value = {
        "geojson": feature_collection(),
        "cvegeo_list": ["09001"],
        "met_zone_code": "09.01",
    }[reference]
    payload = JobCreateRequest(
        metric="root",
        inputs={"location": {"data_type": reference, "value": value}},
        parameters={},
    ).inputs
    backend = InProcessLyraBackend(lambda: runtime)
    with pytest.raises(ToolCallError) as error:
        asyncio.run(
            backend.create_job(
                JobCreateRequest.model_validate(
                    {
                        "metric": "root",
                        "inputs": {
                            k: v for k, v in payload.items() if k != "parameters"
                        },
                        **(
                            {"parameters": payload["parameters"]}
                            if "parameters" in payload
                            else {}
                        ),
                    }
                )
            )
        )
    assert error.value.code == "invalid_parameters"
    enqueue.assert_not_called()


def test_worker_jobs_share_persistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"source": VALUES},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        return pd.DataFrame({"value": [7]})

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={"data": Dependency("source", producer="source")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return data

    _, runtime = install(tmp_path, monkeypatch, [root, source])
    runtime.config.result_store_path = tmp_path / "tables"
    for number, name in enumerate(["source", "root", "root"]):
        worker.initialize_worker(config=runtime.config)
        native = native_job(monkeypatch)
        native.id = f"{number:032x}"
        result = run_and_read(
            worker.run_metric_task,
            accepted_envelope(job_id=native.id, metric=name, inputs={}).model_dump(
                mode="json"
            ),
        )
        assert result["job_id"] == native.id
    assert calls == ["source", "root"]


def test_repeated_submission_to_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root() -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame({"value": [7]})

    _, runtime = install(tmp_path, monkeypatch, [root])
    runtime.config.result_store_path = tmp_path / "tables"
    worker.initialize_worker(config=runtime.config)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    for _ in range(3):
        native = native_job(monkeypatch)
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(metric="root", inputs={}),
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
        assert payload["accepted"]["plan"]["request"] == {
            "metric": "root",
            "inputs": {},
            "parameters": {},
            "interventions": [],
        }
        run_and_read(worker.run_metric_task, payload)
        assert len(calls) == 1


def test_file_graph_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @metric(
        cache="full",
        code_revision="1",
        name="file_source",
        description="file source",
        outputs={"data": VALUES},
    )
    def file_source(context: RunContext) -> pd.DataFrame:
        path = context.temp_dir / "values.csv"
        path.write_text("value\n7\n")
        return pd.read_csv(path)

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="file metric",
        inputs={"data": Dependency("data", producer="file_source")},
        outputs={"answer": VALUES},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    handlers: list[Callable[..., object]] = [root, file_source]
    runtime = install(tmp_path, monkeypatch, handlers)[1]
    worker.initialize_worker(config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs={}),
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


@pytest.mark.parametrize("fails", [False, True])
def test_serialized_worker_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fails: bool
) -> None:
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root() -> pd.DataFrame:
        calls.append("root")
        if fails:
            raise RuntimeError
        return pd.DataFrame({"value": [7]})

    _, runtime = install(tmp_path, monkeypatch, [root])
    runtime.config.result_store_path = tmp_path / "cache"
    worker.initialize_worker(config=runtime.config)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: "33333333333333333333333333333333",
        )
    )
    (envelope,) = enqueue.call_args.args
    payload = envelope.accepted.plan
    assert payload.metric == "root"
    native = native_job(monkeypatch, job_id=envelope.job_id)
    native.args = (envelope.model_dump(mode="json"),)
    if fails:
        with pytest.raises(GraphExecutionError):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert "failure" in native.meta
        assert native.args[0]["accepted"]["plan"] == payload.model_dump(mode="json")
        assert calls == ["root"]
        return
    first = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    native.id = new_job_id()
    second = run_and_read(
        worker.run_metric_task, envelope.model_dump(mode="json") | {"job_id": native.id}
    )
    assert first["outputs"] == second["outputs"] == {"answer": [{"value": 7}]}
    assert calls == ["root"]
    assert envelope.accepted.plan.metric == "root"
    assert native.meta["execution"]["observations"][0]["outcome"] == "cache_hit"


@pytest.mark.parametrize("changed", [False, True])
def test_worker_normalized_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, changed: bool
) -> None:
    work = canonical_workload("routed", 2, 2)
    expected = work.executor.execute(work.prepared).outputs
    config = load_test_config(tmp_path)
    plugin = work.executor.catalog.implementations["bench"].definition
    installed = InstalledPlugin("bench", plugin)
    monkeypatch.setattr(
        "lyra.server.plugins.discover_installed_plugins", lambda _: [installed]
    )
    worker.initialize_worker(config=config)
    native = native_job(monkeypatch)
    jobs = Mock(spec=job_store.JobStore)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(
                metric="two",
                inputs={
                    k: v
                    for k, v in {
                        item.name: {
                            "provider": item.binding.source,
                            "ids": list(item.ids),
                        }
                        for item in work.prepared.inputs
                    }.items()
                    if k != "parameters"
                },
                parameters={
                    item.name: {
                        "provider": item.binding.source,
                        "ids": list(item.ids),
                    }
                    for item in work.prepared.inputs
                }.get("parameters", {}),
            ),
            catalog=registry.Catalog.load(config.plugins),
            jobs=jobs,
            job_id_factory=lambda: native.id,
        )
    )
    (envelope,) = jobs.enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    if changed:
        revised = canonical_workload("routed", 2, 2, revision="target")
        installed = replace(
            installed,
            definition=revised.executor.catalog.implementations["bench"].definition,
        )
        worker.initialize_worker(config=config)
        with pytest.raises(worker.BoundPlanError, match="provider scopes"):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert "failure" in native.meta
    else:
        result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert result["outputs"] == serialize_outputs("two", expected)
        native.meta["execution"]
        assert envelope.accepted.plan.request.inputs


def test_incompatible_plan_failure_replaces_worker_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": TABLE},
    )
    def root() -> pd.DataFrame:
        calls.append("root")
        return value_frame()

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    payload = accepted_envelope(job_id=native.id, metric="root", inputs={}).model_dump(
        mode="json"
    )
    worker.run_metric_task(payload)
    assert native.meta["execution"]["succeeded"]
    payload["accepted"]["plan"]["has_parameters"] = True
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    with pytest.raises(worker.BoundPlanError, match="Selected computation"):
        worker.run_metric_task(payload)
    acquire.assert_not_called()
    assert calls == ["root"]
    assert native.meta["failure"]["error"]["type"] == "bound_plan"
    assert "execution" not in native.meta


def test_isolated_submission_worker_and_result_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    catalog = registry.Catalog(runner.catalog)
    jobs = Mock()
    request = JobCreateRequest(
        metric="count", inputs={"places": {"provider": "target", "ids": ["a"]}}
    )
    response = asyncio.run(
        job_submission.submit_job(
            request,
            catalog=catalog,
            jobs=jobs,
            job_id_factory=lambda: "55555555555555555555555555555555",
        )
    )
    assert response.job_id == "55555555555555555555555555555555"
    assert events == []
    envelope = JobEnvelope.model_validate_json(
        jobs.enqueue.call_args.args[0].model_dump_json()
    )
    config = load_test_config(tmp_path)
    config.result_store_path = tmp_path
    monkeypatch.setattr(
        worker,
        "_WORKER_RUNTIME",
        worker.WorkerRuntime(catalog, config),
    )
    native = Mock(
        id="55555555555555555555555555555555",
        origin="default",
        meta={},
        started_at=None,
        ended_at=None,
        worker_name="worker",
    )
    result = worker.execute_job(envelope.model_dump(mode="json"), cast("Job", native))
    outputs = read_retained(str(result["job_id"])).tables
    assert outputs["result"].total.tolist() == [1]
    assert events == ["setup:target", "resolve:target", "metric", "close:target"]
    native.save_meta.assert_called_once()


@pytest.mark.parametrize("revision", [None, "2", "02", " opaque / β \n"])
def test_optional_revision_worker_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str | None
) -> None:
    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        code_revision=revision,
    )
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    result = run_and_read(worker.execute_job, envelope.model_dump(mode="json"), native)
    assert result["outputs"]["answer"]


@pytest.mark.parametrize(("before", "after"), [(None, "2"), ("2", "02"), ("2", " 2 ")])
def test_worker_rejects_changed_opaque_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: str | None,
    after: str,
) -> None:
    calls = Mock()

    def declare(revision: str | None) -> "Callable[[], pd.DataFrame]":
        @metric(
            name="root",
            description="Root",
            outputs={"answer": VALUES},
            code_revision=revision,
        )
        def root() -> pd.DataFrame:
            calls()
            return pd.DataFrame({"value": [7]})

        return root

    install(tmp_path, monkeypatch, [declare(before)])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    install(tmp_path, monkeypatch, [declare(after)])
    with pytest.raises(worker.BoundPlanError, match="Selected computation"):
        worker.execute_job(envelope.model_dump(mode="json"), cast("Job", native))
    calls.assert_not_called()
