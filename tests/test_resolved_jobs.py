"""Resolved queue boundaries use fake job storage and no service acquisition."""

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, cast
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import DatabaseIO, Dependency, Input, LocationInput, Resource, metric
from lyra.sdk.arguments import PreparedResolvedInvocation, serialize_resolved_input
from lyra.sdk.errors import MetricInputError
from lyra.sdk.job_inputs import prepare_resolved_job_input
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.plugin import PluginDefinition
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lyra_app import worker
from tests.resource_helpers import database_override
from tests.test_scoped_execution import resolved
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

    definition = PluginDefinition(nodes=[root])
    caller = {"parameters": {"value": 7}}
    local = definition.prepare_resolved_invocation("root", caller)
    payload = json.loads(json.dumps(serialize_resolved_input(local)))
    assert payload["parameters"] == {"value": 8}
    if tampered:
        payload["parameters"]["value"] = 9
        with pytest.raises(MetricInputError, match="parameters"):
            prepare_resolved_job_input(definition, "root", payload, caller)
    else:
        prepared = prepare_resolved_job_input(definition, "root", payload, caller)
        assert prepared.identity == local.identity
        assert prepared.arguments() == local.arguments()


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
    sentinel = Mock(side_effect=AssertionError("location resolution in worker"))
    monkeypatch.setattr("lyra.sdk.location_resolution.resolve_locations", sentinel)
    monkeypatch.setattr("lyra.sdk.locations.LocationProvider.__init__", sentinel)

    @metric(name="source", description="Source", outputs={"source": VALUES})
    def source(
        location: LocationInput,
        parameters: Parameters,
        database: Annotated[DatabaseIO, Resource("database")],
    ) -> pd.DataFrame:
        seen.append(("source", location, parameters.value))
        return database.query_dataframe("SELECT value")

    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "data": Dependency(
                "source", arguments={"locations": {"location": Input("lots")}}
            )
        },
    )
    def root(
        blocks: LocationInput,
        lots: LocationInput,
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
    lots = resolved().model_copy(
        update={"collection": "example:lots", "unit_type": "lot"}
    )
    invocation = definition.prepare_resolved_invocation(
        "root", {"blocks": resolved(), "lots": lots, "parameters": {"value": 7}}
    )
    caller: dict[str, object] = {
        name: {
            "collection": value.collection,
            "unit_type": value.unit_type,
            "ids": [feature.id for feature in value.location.features],
        }
        for name, value in invocation.locations.items()
    }
    caller["parameters"] = {"value": 7}
    native.meta["provenance"]["input"] = caller
    envelope = JobEnvelope(
        job_id=native.id, metric="root", input=serialize_resolved_input(invocation)
    )
    yield definition, invocation, envelope, native, seen, sentinel, adapter
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def test_round_trip_identity_arguments_execution_and_cache(queued: Queued) -> None:
    definition, local, envelope, native, seen, sentinel, adapter = queued
    payload = json.loads(envelope.model_dump_json())
    prepared = prepare_resolved_job_input(
        definition, "root", payload["input"], native.meta["provenance"]["input"]
    )
    assert prepared.identity == local.identity
    assert prepared.arguments() == local.arguments()
    assert prepared.locations == local.locations
    assert payload["input"]["parameters"] == {"value": 7}
    assert "ids" not in payload["input"]["lots"]
    for _ in range(2):
        result = worker.execute_resolved_job(payload, cast("Job", native))
        assert result["outputs"] == {"answer": [{"value": 7}]}
        assert isinstance(result["provenance"], dict)
        assert result["provenance"]["input"] == native.meta["provenance"]["input"]
    assert seen == [
        ("source", local.locations["lots"].location, 7),
        (
            "root",
            local.locations["blocks"].location,
            local.locations["lots"].location,
            7,
        ),
    ]
    adapter.query_dataframe.assert_called_once()
    sentinel.assert_not_called()


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
            location["location"]["features"][0]["id"] = "unknown"
        case "duplicate":
            location["location"]["features"].append(location["location"]["features"][0])
        case "missing_input":
            del payload["input"]["lots"]
        case "extra_input":
            payload["input"]["other"] = location
        case "resource":
            location["location"]["features"][0]["properties"]["client"] = Mock()
        case "credentials":
            location["credentials"] = {"password": "private"}
    with pytest.raises((MetricInputError, ValidationError)):
        worker.execute_resolved_job(payload, cast("Job", native))
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
        worker.execute_resolved_job(
            envelope.model_dump(mode="json"), cast("Job", native)
        )
    assert not seen
    sentinel.assert_not_called()


@pytest.mark.parametrize("value", [Mock(), pytest.param(object(), id="live-object")])
def test_serializer_rejects_live_objects(queued: Queued, value: object) -> None:
    _, invocation, _, _, _, _, _ = queued
    invocation.locations["lots"].location.features[0].properties["client"] = value
    with pytest.raises(ValidationError):
        serialize_resolved_input(invocation)


def test_location_free_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = Mock(side_effect=AssertionError("unexpected provider acquisition"))
    monkeypatch.setattr("lyra.sdk.locations.LocationProvider.__init__", sentinel)

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    definition = worker.RUNNER_REGISTRY["root"].definition
    invocation = definition.prepare_resolved_invocation("root", {})
    assert serialize_resolved_input(invocation) == {}
    envelope = JobEnvelope(job_id=native.id, metric="root", input={})
    try:
        result = worker.execute_resolved_job(
            envelope.model_dump(mode="json"), cast("Job", native)
        )
        assert result["outputs"] == {"answer": [{"value": 7}]}
        sentinel.assert_not_called()
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)
