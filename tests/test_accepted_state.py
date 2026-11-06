"""Acceptance owns its values and preserves supplied fields across boundaries."""

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
from lyra.contracts.bound_plan import deserialize_bound_plan, serialize_bound_plan
from lyra.contracts.jobs import AcceptedJob, JobCreateRequest, JobEnvelope
from lyra.contracts.plugins import MetricManifest, PluginInfo, PluginManifest
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog, compile_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.job_submission import submit_job
from lyra.server.registry import Catalog


def parameter_catalog() -> CompiledCatalog:
    node = MetricManifest(
        name="root",
        description="Root",
        parameters_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "default": {},
                    "properties": {
                        "values": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "default": [1],
                        },
                    },
                },
                "nullable": {"type": ["integer", "null"], "default": 7},
            },
        },
        outputs={"result": {"columns": []}},
    )
    return compile_catalog(
        {
            "fixture": PluginManifest(
                plugin=PluginInfo(name="fixture", version="1"),
                factory="fixture:create",
                nodes=[node],
            )
        }
    )


@pytest.mark.parametrize("model_request", [False, True])
@pytest.mark.parametrize(
    "parameters",
    [None, {}, {"options": {}}, {"options": {"values": [1]}, "nullable": None}],
)
def test_supplied_fields_survive_defaults_and_storage(
    parameters: dict | None, *, model_request: bool
) -> None:
    raw: dict = {"metric": "root"}
    if parameters is not None:
        raw["parameters"] = deepcopy(parameters)
    request = ExecutionRequest.model_validate(raw) if model_request else raw
    plan = prepare_execution(parameter_catalog(), request)
    stored = deserialize_bound_plan(serialize_bound_plan(plan))
    assert stored.request.supplied_fields == raw.keys()
    assert stored.request.parameters == (parameters or {})
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
    schema = plan.contracts.nodes["root"].parameters_schema
    assert schema is not None
    schema.clear()
    plan.output_metadata["result"].columns.clear()
    accepted.plugin.name = "changed"
    view = envelope.model_dump(mode="json")
    view["accepted"]["plan"]["request"]["parameters"].clear()
    restored = JobEnvelope.model_validate(snapshot)
    snapshot["accepted"]["plan"]["parameters"].clear()
    assert envelope.model_dump(mode="json") == restored.model_dump(mode="json")
    assert restored.accepted.plan.request.parameters == {"options": {"values": [2]}}
    field = "node"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(plan, field, "other")  # Exercise runtime frozen-field enforcement.


@pytest.mark.parametrize(
    "change", ["presence", "cache_revision", "extra", "parameters", "contracts"]
)
def test_invalid_persisted_state_is_rejected(change: str) -> None:
    payload = prepare_execution(parameter_catalog(), {"metric": "root"}).model_dump(
        mode="json"
    )
    if change == "presence":
        payload["request"]["supplied_fields"] = ["not_a_field"]
    elif change == "cache_revision":
        payload["request"]["cache_revision"] = " "
    elif change == "extra":
        payload["unexpected"] = True
    elif change == "parameters":
        payload["parameters"] = []
    else:
        payload["contracts"]["nodes"].clear()
    with pytest.raises(ValidationError):
        PreparedExecution.model_validate(payload)


@pytest.mark.parametrize("explicit", [False, True])
def test_http_adapter_captures_field_presence(*, explicit: bool) -> None:
    catalog = Catalog(parameter_catalog(), {"root": "default"})
    jobs = Mock()
    data: dict = {"metric": "root", "input": {}}
    if explicit:
        data.update(edition=None, cache_revision=None, input={"parameters": {}})
    asyncio.run(
        submit_job(JobCreateRequest.model_validate(data), catalog=catalog, jobs=jobs)
    )
    (envelope,) = jobs.enqueue.call_args.args
    supplied = envelope.accepted.plan.request.supplied_fields
    assert ("parameters" in supplied) == explicit
    assert ("edition" in supplied) == explicit
    assert ("cache_revision" in supplied) == explicit
    assert envelope.accepted.plan.request.parameters == {}
    assert envelope.accepted.plan.parameters["nullable"] == 7


@pytest.mark.parametrize("explicit", [False, True])
def test_local_adapter_captures_field_presence(*, explicit: bool) -> None:
    class Parameters(MetricParameters):
        number: int = Field(default=7, description="Number")

    @metric(name="root", description="Root", outputs={"result": pa.DataFrameSchema({})})
    def root(parameters: Parameters) -> pd.DataFrame:
        assert parameters.number == 7
        return pd.DataFrame()

    runner = LocalRunner.from_definitions({"fixture": PluginDefinition(metrics=[root])})
    kwargs = {"edition": None, "cache_revision": None} if explicit else {}
    runner.run("root", {"parameters": {}} if explicit else {}, **kwargs)
    record = runner.inspect()
    assert record is not None
    assert record.accepted is not None
    assert ("parameters" in record.accepted.request.supplied_fields) == explicit
    assert ("edition" in record.accepted.request.supplied_fields) == explicit
    assert ("cache_revision" in record.accepted.request.supplied_fields) == explicit
