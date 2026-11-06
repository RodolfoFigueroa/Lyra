"""Selection queue boundaries use fake job storage and managed resources."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, cast
from unittest.mock import Mock, create_autospec

import pandas as pd
import pytest
from lyra.sdk import Dependency, Input, LocalRunner, Locations, Resource, metric
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.bound_plan import deserialize_bound_plan, serialize_bound_plan
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.preparation import PreparationError, prepare_execution
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lyra_app import worker
from tests.location_helpers import node_contract
from tests.queue_helpers import accepted_envelope, submitted_input
from tests.resource_helpers import QueryIO, database_override
from tests.test_location_resolution import Provider, features
from tests.test_worker_graph_integration import VALUES, install, native_job

if TYPE_CHECKING:
    from rq.job import Job


class Parameters(BaseModel):
    """An excluded field still participates in effective execution identity."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    value: int = Field(default=7, exclude=True, description="Value")


class TransformingParameters(Parameters):
    """Supported author validation may transform an input value."""

    @field_validator("value")
    @classmethod
    def increment(cls, value: int) -> int:
        """Return the effective value after author validation."""
        return value + 1


def test_effective_parameters_round_trip() -> None:
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root(parameters: TransformingParameters) -> pd.DataFrame:
        return pd.DataFrame({"value": [parameters.value]})

    plugins = {"test": PluginDefinition(metrics=[root])}
    runner = LocalRunner.from_definitions(plugins)
    request = {"metric": "root", "parameters": {"value": 7}}
    prepared = prepare_execution(runner.catalog, request)
    restored = deserialize_bound_plan(serialize_bound_plan(prepared))
    assert restored.parameters == {"value": 7}
    result = GraphExecutor(runner.catalog, plugins, Mock()).execute(restored)
    assert result["answer"]["value"].tolist() == [8]
    pd.testing.assert_frame_equal(
        result["answer"], runner.run("root", {"parameters": {"value": 7}})["answer"]
    )


Queued = tuple[
    PluginDefinition,
    PreparedResolvedInvocation,
    JobEnvelope,
    SimpleNamespace,
    list[tuple],
    Mock,
    Mock,
]


@pytest.fixture
def queued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Queued]:
    seen = []
    adapter = Mock()
    adapter.query_dataframe.return_value = pd.DataFrame({"value": [7]})
    resource = database_override(adapter)
    sentinel = create_autospec(Provider.resolve, side_effect=Provider.resolve)
    monkeypatch.setattr(Provider, "resolve", sentinel)

    @metric(
        locations={"location": node_contract()},
        name="source",
        description="Source",
        outputs={"source": VALUES},
    )
    def source(
        location: Locations,
        parameters: Parameters,
        database: Annotated[QueryIO, Resource("database")],
    ) -> pd.DataFrame:
        seen.append(("source", location, parameters.value))
        return database.query_dataframe("SELECT value")

    @metric(
        locations={"blocks": node_contract(), "lots": node_contract()},
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "data": Dependency(
                "source", locations={"location": Input("lots")}, producer="source"
            )
        },
    )
    def root(
        blocks: Locations,
        lots: Locations,
        parameters: Parameters,
        data: pd.DataFrame,
    ) -> pd.DataFrame:
        seen.append(("root", blocks, lots, parameters.value))
        return data

    _, runtime = install(tmp_path, monkeypatch, [root, source], {"database": resource})
    runtime.config.cache_dir = tmp_path / "cache"
    worker.refresh_runner_registry("heavy", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    entry = worker.RUNNER_REGISTRY["root"]
    definition = entry.implementations[entry.distribution]
    provider = PluginDefinition(
        metrics=[], resources={"locations": Provider}, location_provider="locations"
    )
    entry = worker.RUNNER_REGISTRY["root"]
    entry.implementations["provider"] = provider
    assert provider.location_provider is not None
    worker.RUNNER_REGISTRY["root"] = replace(
        entry,
        catalog=compile_catalog(
            {
                **entry.catalog.manifests,
                "provider": provider.manifest(
                    plugin=PluginInfo(name="provider", version="1"),
                    factory="provider:create",
                ),
            }
        ),
    )
    invocation = definition.prepare_resolved_invocation(
        "root",
        {
            "blocks": features(["a"]).with_contract(Provider.contracts[0]),
            "lots": features(["b"]).with_contract(Provider.contracts[0]),
            "parameters": {"value": 7},
        },
    )
    caller: dict[str, object] = {
        name: {
            "collection": value.collection,
            "unit_type": value.unit_type,
            "ids": list(value.ids),
        }
        for name, value in invocation.locations.items()
    }
    caller["parameters"] = {"value": 7}
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs=caller)
    yield definition, invocation, envelope, native, seen, sentinel, adapter
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def test_round_trip_identity_arguments_execution_and_cache(queued: Queued) -> None:
    _, local, envelope, native, seen, sentinel, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    prepared = envelope.accepted.plan
    assert prepared.parameters == {"value": 7}
    assert next(item.ids for item in prepared.inputs if item.name == "lots") == ("b",)
    assert payload["accepted"]["plan"]["request"]["parameters"] == {"value": 7}
    assert payload["accepted"]["plan"]["request"]["inputs"]["lots"]["ids"] == ["b"]
    for _ in range(2):
        result = worker.execute_job(payload, cast("Job", native))
        assert result["outputs"] == {"answer": [{"value": 7}]}
        assert "provenance" not in result
        assert native.meta["execution"]["succeeded"]
    assert seen == [
        ("source", local.locations["lots"], 7),
        (
            "root",
            local.locations["blocks"],
            local.locations["lots"],
            7,
        ),
    ]
    adapter.query_dataframe.assert_called_once()
    assert sentinel.call_count == 4


@pytest.mark.parametrize(
    "issue",
    [
        "missing_scope",
        "collection",
        "unit_type",
        "ids",
        "duplicate",
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
            del location["collection"]
        case "collection":
            location["collection"] = "other:lots"
        case "unit_type":
            location["unit_type"] = "block"
        case "ids":
            location["ids"][0] = "unknown"
        case "duplicate":
            location["ids"].append(location["ids"][0])
        case "missing_input":
            del request["inputs"]["lots"]
        case "extra_input":
            request["inputs"]["other"] = location
        case "resource":
            location["client"] = "forbidden"
        case "credentials":
            location["credentials"] = {"password": "private"}
    with pytest.raises(ValueError, match=r"validation error|disagree"):
        worker.execute_job(payload, cast("Job", native))
    assert not seen
    sentinel.assert_not_called()
    adapter.query_dataframe.assert_not_called()


@pytest.mark.parametrize(
    "issue", ["job_id", "queue", "contract", "implementation", "provider"]
)
def test_job_integrity_checks(queued: Queued, issue: str) -> None:
    _, _, envelope, native, seen, sentinel, _ = queued
    entry = worker.RUNNER_REGISTRY["root"]
    match issue:
        case "job_id":
            native.id = "wrong"
        case "queue":
            native.origin = "wrong"
        case "contract":
            contract = entry.catalog.graph.nodes["root"].node
            assert contract.kind == "metric"
            contract.outputs["answer"].meaning = "changed"
            worker.RUNNER_REGISTRY["root"] = replace(
                entry, catalog=compile_catalog(entry.catalog.manifests)
            )
        case "implementation":
            entry.implementations.pop(entry.distribution)
        case "provider":
            entry.implementations.pop("provider")
    with pytest.raises(worker.BoundPlanError):
        worker.execute_job(envelope.model_dump(mode="json"), cast("Job", native))
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

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    try:
        result = worker.execute_job(
            envelope.model_dump(mode="json"), cast("Job", native)
        )
        assert result["outputs"] == {"answer": [{"value": 7}]}
        sentinel.assert_not_called()
    finally:
        worker.RUNNER_REGISTRY.clear()
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
        worker.run_metric_task(payload)
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
    worker.run_metric_task(payload)
    assert native.meta["execution"]["succeeded"]
    with pytest.raises(worker.BoundPlanError):
        worker.run_metric_task({**payload, "job_id": "different"})
    assert "execution" not in native.meta
    assert native.args[0] == payload
    assert native.meta["failure"]["error"]["type"] == "bound_plan"
