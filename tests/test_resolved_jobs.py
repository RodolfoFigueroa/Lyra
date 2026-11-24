"""Selection queue boundaries use fake job storage and managed resources."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, cast
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.dependencies import Input
from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import Resource
from lyra.server import worker
from test_support.location_resolution import Provider, location_value
from test_support.runtime_inputs import native_inputs
from tests.queue_helpers import accepted_envelope, run_and_read, submitted_input
from tests.resource_helpers import QueryIO, database_override
from tests.worker_graph_integration_helpers import (
    VALUES,
    install,
    install_definitions,
    native_job,
)

if TYPE_CHECKING:
    from rq.job import Job


class Parameters(BaseModel):
    """Every effective field participates in execution identity."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    value: int = Field(default=7, description="Value")


class TransformingParameters(Parameters):
    """Supported author validation may transform an input value."""

    @field_validator("value")
    @classmethod
    def normalize_even(cls, value: int) -> int:
        """Return the effective value after author validation."""
        return value + value % 2


def test_effective_parameters_round_trip() -> None:
    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root(parameters: TransformingParameters) -> pd.DataFrame:
        return pd.DataFrame({"value": [parameters.value]})

    plugins = {"test": PluginDefinition(metrics=[root])}
    runner = LocalRunner.from_definitions(plugins)
    request = {"metric": "root", "parameters": {"value": 7}}
    prepared = prepare_execution(runner.catalog, request)
    restored = PreparedExecution.model_validate_json(prepared.model_dump_json())
    assert restored.parameters == {"value": 8}
    result = (
        GraphExecutor(
            runner.catalog,
            Mock(),
        )
        .execute(restored)
        .outputs
    )
    assert result["answer"]["value"].tolist() == [8]
    pd.testing.assert_frame_equal(
        result["answer"],
        runner.run("root", inputs={}, parameters={"value": 7}).tables["answer"],
    )


Queued = tuple[
    BoundPlugin,
    PreparedResolvedInvocation,
    JobEnvelope,
    SimpleNamespace,
    list[tuple],
    Mock,
    Mock,
]


def queued_root(seen: list[tuple], *, nullable: bool = False) -> Callable:
    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES.update_column("value", nullable=nullable)},
        inputs={
            "blocks": LocationDependency(provider=Provider.id),
            "lots": LocationDependency(provider=Provider.id),
            "data": Dependency("source", producer="source").bind(
                location=Input("lots")
            ),
        },
    )
    def root(
        blocks: gpd.GeoDataFrame,
        lots: gpd.GeoDataFrame,
        parameters: Parameters,
        data: pd.DataFrame,
    ) -> pd.DataFrame:
        seen.append(
            (
                "root",
                tuple(blocks.location_id),
                tuple(lots.location_id),
                parameters.value,
            )
        )
        return data

    return root


@pytest.fixture
def queued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Queued]:
    seen = []
    adapter = Mock()
    adapter.query_dataframe.return_value = pd.DataFrame({"value": [7]})
    resource = database_override(adapter)
    sentinel = create_autospec(Provider.resolve, side_effect=Provider.resolve)
    monkeypatch.setattr(Provider, "resolve", sentinel)

    @metric(
        cache="full",
        code_revision="1",
        inputs={"location": LocationDependency(provider=Provider.id)},
        name="source",
        description="Source",
        outputs={"source": VALUES},
    )
    def source(
        location: gpd.GeoDataFrame,
        parameters: Parameters,
        database: Annotated[QueryIO, Resource("database")],
    ) -> pd.DataFrame:
        seen.append(("source", tuple(location.location_id), parameters.value))
        return database.query_dataframe("SELECT value")

    definitions = {
        "plugin_0": PluginDefinition(
            metrics=[queued_root(seen)], resources={"database": resource}
        ),
        "plugin_1": PluginDefinition(
            metrics=[source], resources={"database": resource}
        ),
        "provider": PluginDefinition(resources={"locations": Provider}),
    }
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    runtime.config.result_store_path = tmp_path / "cache"
    worker.initialize_worker("heavy", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    catalog = worker.require_worker_runtime().catalog.compiled
    definition = catalog.implementations[catalog.graph.nodes["root"].distribution]
    invocation = definition.prepare_resolved_invocation(
        "root",
        native_inputs(
            {
                "blocks": location_value("a"),
                "lots": location_value("b"),
                "parameters": {"value": 7},
            }
        ),
    )
    caller: dict[str, object] = {
        name: {
            "provider": value.contract.provider_id,
            "ids": list(value.ids),
        }
        for name, value in invocation.locations.items()
    }
    caller["parameters"] = {"value": 7}
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs=caller)
    yield definition, invocation, envelope, native, seen, sentinel, adapter
    worker.set_runner_temp_base(None)


def test_round_trip_identity_arguments_execution_and_cache(queued: Queued) -> None:
    _, local, envelope, native, seen, sentinel, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    prepared = envelope.accepted.plan
    assert prepared.parameters == {"value": 7}
    assert prepared.request.inputs["lots"].ids == ["b"]
    assert payload["accepted"]["plan"]["request"]["parameters"] == {"value": 7}
    assert payload["accepted"]["plan"]["request"]["inputs"]["lots"]["ids"] == ["b"]
    for warm in (False, True):
        sentinel.reset_mock()
        result = run_and_read(worker.execute_job, payload, cast("Job", native))
        if warm:
            sentinel.assert_not_called()
        else:
            assert sentinel.called
        assert result["outputs"] == {"answer": [{"value": 7}]}
        assert "provenance" not in result
        assert native.meta["execution"]["succeeded"]
    assert seen == [
        ("source", local.locations["lots"].ids, 7),
        (
            "root",
            local.locations["blocks"].ids,
            local.locations["lots"].ids,
            7,
        ),
    ]
    adapter.query_dataframe.assert_called_once()


@pytest.mark.parametrize(
    "issue",
    [
        "missing_scope",
        "provider",
        "invalid_provider",
        "ids",
        "missing_input",
        "extra_input",
        "resource",
        "credentials",
    ],
)
def test_invalid_payload_never_executes_or_resolves(queued: Queued, issue: str) -> None:
    _, _, envelope, native, seen, sentinel, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    bound = payload["accepted"]["plan"]
    request = bound["request"]
    location = request["inputs"]["lots"]
    match issue:
        case "missing_scope":
            del location["provider"]
        case "provider":
            location["provider"] = "other:lots"
        case "invalid_provider":
            location["provider"] = 1
        case "ids":
            location["ids"][0] = ""
        case "missing_input":
            del request["inputs"]["lots"]
        case "extra_input":
            request["inputs"]["other"] = location
        case "resource":
            location["client"] = "forbidden"
        case "credentials":
            location["credentials"] = {"password": "private"}
    with pytest.raises(
        ValueError, match=r"validation error|disagree|requires provider|Expected inputs"
    ):
        run_and_read(worker.execute_job, payload, cast("Job", native))
    assert not seen
    sentinel.assert_not_called()
    adapter.query_dataframe.assert_not_called()


@pytest.mark.parametrize("issue", ["job_id", "queue"])
def test_job_integrity_checks(queued: Queued, issue: str) -> None:
    _, _, envelope, native, seen, sentinel, _ = queued
    if issue == "job_id":
        native.id = "wrong"
    else:
        native.origin = "wrong"
    with pytest.raises(worker.BoundPlanError):
        run_and_read(
            worker.execute_job, envelope.model_dump(mode="json"), cast("Job", native)
        )
    assert not seen
    sentinel.assert_not_called()


@pytest.mark.parametrize("value", [Mock(), pytest.param(object(), id="live-object")])
def test_queue_rejects_live_objects(queued: Queued, value: object) -> None:
    _, _, envelope, _, _, _, _ = queued
    inputs = submitted_input(envelope)
    inputs["lots"]["client"] = value
    with pytest.raises(PreparationError):
        accepted_envelope(job_id=envelope.job_id, metric="root", inputs=inputs)


def test_location_free_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = Mock(side_effect=AssertionError("unexpected provider acquisition"))
    monkeypatch.setattr("lyra.sdk.locations.LocationProvider.__init__", sentinel)

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    try:
        result = run_and_read(
            worker.execute_job, envelope.model_dump(mode="json"), cast("Job", native)
        )
        assert result["outputs"] == {"answer": [{"value": 7}]}
        sentinel.assert_not_called()
    finally:
        worker.set_runner_temp_base(None)


@pytest.mark.parametrize("issue", ["missing", "null", "empty", "malformed"])
def test_required_bound_plan_fails_once_before_acquisition(
    queued: Queued,
    issue: str,
) -> None:
    _, _, envelope, native, seen, sentinel, adapter = queued
    payload = envelope.model_dump(mode="json")
    if issue == "missing":
        payload["accepted"].pop("plan")
    else:
        payload["accepted"]["plan"] = {"null": None, "empty": "", "malformed": "{}"}[
            issue
        ]
    with pytest.raises(ValidationError):
        run_and_read(worker.run_metric_task, payload)
    # One worker identity update and one failure update.
    assert native.save_meta.call_count == 2
    assert native.meta["failure"]["job_id"] == native.id
    assert seen == []
    sentinel.assert_not_called()
    adapter.query_dataframe.assert_not_called()


def test_preflight_failure_clears_previous_observations(queued: Queued) -> None:
    _, _, envelope, native, _, _, _ = queued
    payload = envelope.model_dump(mode="json")
    native.args = (payload,)
    run_and_read(worker.run_metric_task, payload)
    assert native.meta["execution"]["succeeded"]
    with pytest.raises(worker.BoundPlanError):
        run_and_read(worker.run_metric_task, {**payload, "job_id": "different"})
    assert "execution" not in native.meta
    assert native.args[0] == payload
    assert native.meta["failure"]["error"]["type"] == "bound_plan"


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("missing_metric", [False, True])
def test_deployment_changed_before_lookup_or_acquisition(
    queued: Queued, *, missing_metric: bool, cached: bool
) -> None:
    _, _, envelope, native, seen, provider, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    if cached:
        result = run_and_read(worker.run_metric_task, payload)
        assert result["outputs"] == {"answer": [{"value": 7}]}
        assert native.meta["execution"]["succeeded"]
        seen.clear()
        provider.reset_mock()
        adapter.reset_mock()
    payload["accepted"]["plan"]["build_revision"] = "older-build"
    if missing_metric:
        payload["accepted"]["plan"]["request"]["metric"] = "missing"
    with pytest.raises(worker.DeploymentChangedError, match="deployment_changed"):
        worker.run_metric_task(payload)
    assert native.meta["failure"]["error"]["type"] == "deployment_changed"
    assert "execution" not in native.meta
    assert seen == []
    provider.assert_not_called()
    adapter.query_dataframe.assert_not_called()


@pytest.mark.parametrize("missing", ["root", "dependency", "field"])
def test_missing_normalized_values_fail_before_acquisition(
    queued: Queued, missing: str
) -> None:
    _, _, envelope, _, seen, provider, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    plan = payload["accepted"]["plan"]
    if missing == "dependency":
        plan["parameter_uses"].pop(
            next(key for key in plan["parameter_uses"] if key != "root")
        )
    elif missing == "root":
        plan["parameter_uses"].pop("root")
    else:
        for values in [
            plan["parameters"],
            plan["request"]["parameters"],
            plan["parameter_uses"]["root"],
        ]:
            values.clear()
    with pytest.raises(ValueError, match=r"normalized|Effective root"):
        worker.run_metric_task(payload)
    assert seen == []
    provider.assert_not_called()
    adapter.query_dataframe.assert_not_called()
