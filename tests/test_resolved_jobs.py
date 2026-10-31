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
from lyra.sdk import DatabaseIO, Dependency, Input, Locations, Resource, metric
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.errors import MetricInputError
from lyra.sdk.job_inputs import prepare_job_input
from lyra.sdk.locations import SelectedLocationProvider
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.plugin import PluginDefinition
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lyra_app import worker
from tests.resource_helpers import database_override
from tests.test_location_resolution import Provider, features, selection
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


@pytest.mark.parametrize("tampered", [False, True])
def test_effective_parameters_round_trip(*, tampered: bool) -> None:
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root(parameters: TransformingParameters) -> pd.DataFrame:
        return pd.DataFrame({"value": [parameters.value]})

    definition = PluginDefinition(metrics=[root])
    caller = {"parameters": {"value": 7}}
    local = definition.prepare_resolved_invocation("root", caller)
    payload = json.loads(json.dumps(caller))
    assert payload["parameters"] == {"value": 7}
    if tampered:
        payload["parameters"]["value"] = 9
        with pytest.raises(MetricInputError, match="input"):
            prepare_job_input(definition, "root", payload, caller)
    else:
        prepared = prepare_job_input(definition, "root", payload, caller)
        assert prepared.parameters == local.parameters
        assert isinstance(prepared.parameters, TransformingParameters)
        assert prepared.parameters.value == 8


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
    monkeypatch.setattr(worker, "WorkerDatabaseIO", database_override(adapter))
    sentinel = create_autospec(Provider.resolve, side_effect=Provider.resolve)
    monkeypatch.setattr(Provider, "resolve", sentinel)

    @metric(name="source", description="Source", outputs={"source": VALUES})
    def source(
        location: Locations,
        parameters: Parameters,
        database: Annotated[DatabaseIO, Resource("database")],
    ) -> pd.DataFrame:
        seen.append(("source", location, parameters.value))
        return database.query_dataframe("SELECT value")

    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={"data": Dependency("source", locations={"location": Input("lots")})},
    )
    def root(
        blocks: Locations,
        lots: Locations,
        parameters: Parameters,
        data: pd.DataFrame,
    ) -> pd.DataFrame:
        seen.append(("root", blocks, lots, parameters.value))
        return data

    _, runtime = install(tmp_path, monkeypatch, [root, source])
    runtime.config.cache_dir = tmp_path / "cache"
    worker.refresh_runner_registry(
        "heavy", config=runtime.config, credentials=worker.require_worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    native = native_job(monkeypatch)
    definition = worker.RUNNER_REGISTRY["root"].definition
    provider = PluginDefinition(
        metrics=[], resources={"locations": Provider}, location_provider="locations"
    )
    entry = worker.RUNNER_REGISTRY["root"]
    entry.implementations["provider"] = provider
    assert provider.location_provider is not None
    worker.RUNNER_REGISTRY["root"] = replace(
        entry,
        location_provider=SelectedLocationProvider(
            "provider", provider.location_provider
        ),
    )
    invocation = definition.prepare_resolved_invocation(
        "root",
        {
            "blocks": features(["a"]),
            "lots": features(["b"]),
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
    native.meta["provenance"]["input"] = caller
    envelope = JobEnvelope(job_id=native.id, metric="root", input=caller)
    yield definition, invocation, envelope, native, seen, sentinel, adapter
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def test_round_trip_identity_arguments_execution_and_cache(queued: Queued) -> None:
    definition, local, envelope, native, seen, sentinel, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    prepared = prepare_job_input(
        definition, "root", payload["input"], native.meta["provenance"]["input"]
    )
    assert prepared.parameters == local.parameters
    assert prepared.parameters == Parameters(value=7)
    assert prepared.selections["lots"] == selection("b")
    assert payload["input"]["parameters"] == {"value": 7}
    assert payload["input"]["lots"]["ids"] == ["b"]
    for _ in range(2):
        result = worker.execute_job(payload, cast("Job", native))
        assert result["outputs"] == {"answer": [{"value": 7}]}
        assert isinstance(result["provenance"], dict)
        assert result["provenance"]["input"] == native.meta["provenance"]["input"]
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
    location = payload["input"]["lots"]
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
            del payload["input"]["lots"]
        case "extra_input":
            payload["input"]["other"] = location
        case "resource":
            location["client"] = Mock()
        case "credentials":
            location["credentials"] = {"password": "private"}
    with pytest.raises((MetricInputError, ValidationError)):
        worker.execute_job(payload, cast("Job", native))
    assert not seen
    sentinel.assert_not_called()
    adapter.query_dataframe.assert_not_called()


@pytest.mark.parametrize(
    "issue",
    ["job_id", "queue", "contract", "distribution", "plugin", "metric", "outputs"],
)
def test_job_integrity_checks(queued: Queued, issue: str) -> None:
    _, _, envelope, native, seen, sentinel, _ = queued
    match issue:
        case "job_id":
            native.id = "wrong"
        case "queue":
            native.origin = "wrong"
        case "contract" | "distribution":
            native.meta[issue] = "wrong"
        case "plugin" | "metric":
            native.meta["provenance"][issue] = "wrong"
        case "outputs":
            native.meta["provenance"]["outputs"] = {
                "wrong": worker.RUNNER_REGISTRY["root"].contract.outputs["answer"]
            }
    with pytest.raises((RuntimeError, ValidationError)):
        worker.execute_job(envelope.model_dump(mode="json"), cast("Job", native))
    assert not seen
    sentinel.assert_not_called()


@pytest.mark.parametrize("value", [Mock(), pytest.param(object(), id="live-object")])
def test_queue_rejects_live_objects(queued: Queued, value: object) -> None:
    definition, _, envelope, native, _, _, _ = queued
    envelope.input["lots"]["client"] = value
    with pytest.raises(MetricInputError, match="JSON"):
        prepare_job_input(
            definition, "root", envelope.input, native.meta["provenance"]["input"]
        )


def test_location_free_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = Mock(side_effect=AssertionError("unexpected provider acquisition"))
    monkeypatch.setattr("lyra.sdk.locations.LocationProvider.__init__", sentinel)

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = JobEnvelope(job_id=native.id, metric="root", input={})
    try:
        result = worker.execute_job(
            envelope.model_dump(mode="json"), cast("Job", native)
        )
        assert result["outputs"] == {"answer": [{"value": 7}]}
        sentinel.assert_not_called()
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)
