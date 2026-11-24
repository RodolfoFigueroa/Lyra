"""Acceptance owns complete effective values across boundaries."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import Field, ValidationError

from lyra.contracts.accepted import ExecutionRequest, PreparedExecution, WorkerRequest
from lyra.contracts.jobs import AcceptedJob, JobCreateRequest, JobEnvelope
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.job_submission import MetricPayloadValidationError, submit_job
from lyra.server.registry import Catalog
from test_support.table_fixtures import value_frame, value_schema


def parameter_catalog() -> CompiledCatalog:
    class Options(MetricParameters):
        values: list[int] = Field(default_factory=lambda: [1], description="Values")

    class Parameters(MetricParameters):
        options: Options = Field(default_factory=Options, description="Options")
        nullable: int | None = Field(7, description="Nullable value")

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"result": value_schema()},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        assert parameters.options.model_dump()
        return value_frame()

    return compile_live_catalog({"fixture": PluginDefinition(metrics=[root])})


@pytest.mark.parametrize("model_request", [False, True])
@pytest.mark.parametrize(
    "parameters",
    [None, {}, {"options": {}}, {"options": {"values": [1]}, "nullable": None}],
)
def test_effective_values_survive_defaults_and_storage(
    parameters: dict | None, *, model_request: bool
) -> None:
    raw: dict = {"metric": "root"}
    if parameters is not None:
        raw["parameters"] = deepcopy(parameters)
    request = ExecutionRequest.model_validate(raw) if model_request else raw
    plan = prepare_execution(parameter_catalog(), request)
    stored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert stored.request.parameters == {
        "options": {"values": [1]},
        "nullable": None if parameters and "nullable" in parameters else 7,
    }
    assert stored == plan
    assert PreparedExecution.model_validate(plan.model_dump()) == plan


def test_nested_views_and_constructor_inputs_are_owned() -> None:
    catalog = parameter_catalog()
    raw = {"metric": "root", "parameters": {"options": {"values": [2]}}}
    plan = prepare_execution(catalog, raw)
    accepted = AcceptedJob(
        plan=WorkerRequest.from_plan(plan),
        plugin=catalog.manifests["fixture"].plugin,
        catalog_fingerprint="test",
        created_at=datetime.now(UTC),
    )
    envelope = JobEnvelope(job_id="job", accepted=accepted)
    snapshot = envelope.model_dump(mode="json")
    raw["parameters"]["options"]["values"].append(3)
    catalog.graph.nodes["root"].node.description = "changed"
    cast("dict", plan.request.parameters["options"])["values"].append(4)
    plan.output_metadata["result"].columns.clear()
    accepted.plugin.name = "changed"
    view = envelope.model_dump(mode="json")
    view["accepted"]["plan"]["request"]["parameters"].clear()
    restored = JobEnvelope.model_validate(snapshot)
    snapshot["accepted"]["plan"]["request"]["parameters"].clear()
    assert envelope.model_dump(mode="json") == restored.model_dump(mode="json")
    assert restored.accepted.plan.request.parameters == {
        "options": {"values": [2]},
        "nullable": 7,
    }
    field = "node"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(plan, field, "other")  # Exercise runtime frozen-field enforcement.


@pytest.mark.parametrize("change", ["extra", "parameters", "outputs"])
def test_invalid_persisted_state_is_rejected(change: str) -> None:
    payload = prepare_execution(parameter_catalog(), {"metric": "root"}).model_dump(
        mode="json"
    )
    if change == "extra":
        payload["unexpected"] = True
    elif change == "parameters":
        payload["request"]["parameters"] = []
    else:
        payload["outputs"].clear()
    with pytest.raises(ValidationError):
        PreparedExecution.model_validate(payload)


@pytest.mark.parametrize("explicit", [False, True])
def test_http_adapter_captures_effective_values(*, explicit: bool) -> None:
    catalog = Catalog(parameter_catalog(), {"root": "default"})
    jobs = Mock()
    data: dict = {"metric": "root", "inputs": {}}
    if explicit:
        data.update(parameters={})
    asyncio.run(
        submit_job(JobCreateRequest.model_validate(data), catalog=catalog, jobs=jobs)
    )
    (envelope,) = jobs.enqueue.call_args.args
    assert envelope.accepted.plan.parameter_uses == {}
    assert envelope.accepted.plan.request.parameters["nullable"] == 7


def test_submission_rejects_nested_union_default_that_changes_type_on_replay() -> None:
    class FloatChoice(MetricParameters):
        value: float

    class IntChoice(MetricParameters):
        value: int = 1

    class Parameters(MetricParameters):
        choice: FloatChoice | IntChoice = Field(
            default_factory=IntChoice, description="Numeric choice"
        )

    @metric(
        name="root",
        description="Root",
        outputs={"result": value_schema()},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        pytest.fail(f"Submission invoked the handler with {parameters!r}")

    compiled = compile_live_catalog({"fixture": PluginDefinition(metrics=[root])})
    catalog = Catalog(compiled, {"root": "default"})
    jobs = Mock()
    # The factory constructs IntChoice, but its JSON object matches FloatChoice first.
    with pytest.raises(MetricPayloadValidationError) as error:
        asyncio.run(
            submit_job(JobCreateRequest(metric="root"), catalog=catalog, jobs=jobs)
        )
    assert "revalidation" in error.value.errors[0]["msg"]
    jobs.enqueue.assert_not_called()

    plan = prepare_execution(
        compiled, {"metric": "root", "parameters": {"choice": {"value": 1.5}}}
    )
    stored = WorkerRequest.model_validate_json(
        WorkerRequest.from_plan(plan).model_dump_json()
    )
    replay = prepare_execution(
        compiled,
        stored.request.submission_payload(),
        normalized_uses=stored.parameter_uses,
    )
    assert replay.request.parameters == {"choice": {"value": 1.5}}


@pytest.mark.parametrize("explicit", [False, True])
def test_local_adapter_captures_effective_values(*, explicit: bool) -> None:
    class Parameters(MetricParameters):
        number: int = Field(default=7, description="Number")

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"result": value_schema()},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        assert parameters.number == 7
        return value_frame()

    runner = LocalRunner.from_definitions({"fixture": PluginDefinition(metrics=[root])})
    runner.run("root", parameters={} if explicit else None)
    record = runner.inspect()
    assert record is not None
    assert record.accepted is not None
    assert record.accepted.request.parameters == {"number": 7}


def test_missing_nested_normalized_default_is_rejected() -> None:
    catalog = parameter_catalog()
    plan = prepare_execution(catalog, {"metric": "root"})
    values = plan.model_dump(mode="json")["parameter_uses"]
    request = plan.request.model_dump()
    del request["parameters"]["options"]["values"]
    with pytest.raises(PreparationError, match="Missing normalized"):
        prepare_execution(catalog, request, normalized_uses=values)


def test_empty_and_absent_models_remain_distinct_in_worker_requests() -> None:
    class Empty(MetricParameters):
        pass

    @metric(
        name="declared",
        description="Empty model",
        outputs={"result": value_schema()},
    )
    def declared(parameters: Empty) -> pd.DataFrame:
        assert isinstance(parameters, Empty)
        return value_frame()

    @metric(
        name="absent",
        description="No model",
        outputs={"result": value_schema()},
    )
    def absent() -> pd.DataFrame:
        return value_frame()

    runner = LocalRunner.from_definitions(
        {"fixture": PluginDefinition(metrics=[declared, absent])}
    )
    for metric_name, has_parameters in (("declared", True), ("absent", False)):
        runner.run(metric_name)
        plan = prepare_execution(runner.catalog, {"metric": metric_name})
        stored = WorkerRequest.model_validate_json(
            WorkerRequest.from_plan(plan).model_dump_json()
        )
        assert stored.request.parameters == {}
        assert stored.has_parameters is has_parameters
