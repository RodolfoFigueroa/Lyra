"""Acceptance owns complete effective values across boundaries."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field, ValidationError

from lyra.contracts.accepted import ExecutionRequest, PreparedExecution
from lyra.contracts.jobs import AcceptedJob, JobCreateRequest, JobEnvelope
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.job_submission import submit_job
from lyra.server.registry import Catalog


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
        outputs={"result": pa.DataFrameSchema({})},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        assert parameters.options.model_dump()
        return pd.DataFrame()

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
    assert stored.request.parameters == stored.parameters
    assert stored.parameters == {
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
        plan=plan,
        plugin=catalog.manifests["fixture"].plugin,
        catalog_fingerprint="test",
        created_at=datetime.now(UTC),
    )
    envelope = JobEnvelope(job_id="job", accepted=accepted)
    snapshot = envelope.model_dump(mode="json")
    raw["parameters"]["options"]["values"].append(3)
    catalog.graph.nodes["root"].node.description = "changed"
    cast("dict", plan.request.parameters["options"])["values"].append(4)
    cast("dict", plan.parameters["options"])["values"].append(5)
    plan.output_metadata["result"].columns.clear()
    accepted.plugin.name = "changed"
    view = envelope.model_dump(mode="json")
    view["accepted"]["plan"]["request"]["parameters"].clear()
    restored = JobEnvelope.model_validate(snapshot)
    snapshot["accepted"]["plan"]["parameters"].clear()
    assert envelope.model_dump(mode="json") == restored.model_dump(mode="json")
    assert restored.accepted.plan.request.parameters == {
        "options": {"values": [2]},
        "nullable": 7,
    }
    field = "node"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(plan, field, "other")  # Exercise runtime frozen-field enforcement.


@pytest.mark.parametrize("change", ["cache_revision", "extra", "parameters", "outputs"])
def test_invalid_persisted_state_is_rejected(change: str) -> None:
    payload = prepare_execution(parameter_catalog(), {"metric": "root"}).model_dump(
        mode="json"
    )
    if change == "cache_revision":
        payload["request"]["cache_revision"] = " "
    elif change == "extra":
        payload["unexpected"] = True
    elif change == "parameters":
        payload["parameters"] = []
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
        data.update(cache_revision=None, parameters={})
    asyncio.run(
        submit_job(JobCreateRequest.model_validate(data), catalog=catalog, jobs=jobs)
    )
    (envelope,) = jobs.enqueue.call_args.args
    assert (
        envelope.accepted.plan.request.parameters == envelope.accepted.plan.parameters
    )
    assert envelope.accepted.plan.parameters["nullable"] == 7


@pytest.mark.parametrize("explicit", [False, True])
def test_local_adapter_captures_effective_values(*, explicit: bool) -> None:
    class Parameters(MetricParameters):
        number: int = Field(default=7, description="Number")

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"result": pa.DataFrameSchema({})},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        assert parameters.number == 7
        return pd.DataFrame()

    runner = LocalRunner.from_definitions({"fixture": PluginDefinition(metrics=[root])})
    kwargs = {"cache_revision": None} if explicit else {}
    runner.run("root", parameters={} if explicit else None, **kwargs)
    record = runner.inspect()
    assert record is not None
    assert record.accepted is not None
    assert record.accepted.parameters == {"number": 7}


def test_missing_nested_normalized_default_is_rejected() -> None:
    catalog = parameter_catalog()
    plan = prepare_execution(catalog, {"metric": "root"})
    values = plan.model_dump(mode="json")["parameter_uses"]
    del values["root"]["options"]["values"]
    with pytest.raises(PreparationError, match="Missing normalized"):
        prepare_execution(catalog, plan.request.model_dump(), normalized_uses=values)
