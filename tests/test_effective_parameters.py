"""Acceptance retains complete values for every use before execution."""

import asyncio
import json
from typing import TYPE_CHECKING, Self
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field, field_validator, model_validator

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.dependencies import Input
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.parameters import prepare_parameter_uses, route_key, use_key
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.dependencies import Dependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import main, worker
from test_support.accepted_sources import routed_request
from tests.config_helpers import api_credentials
from tests.invocation_preparation_helpers import implementation
from tests.mcp_helpers import submit_both
from tests.queue_helpers import run_and_read
from tests.worker_graph_integration_helpers import VALUES, install, native_job

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.types import JsonObject


class Filters(MetricParameters):
    tags: list[str] = Field(default_factory=list, description="Tags")
    label: str | None = Field(None, description="Label")


class RootParameters(MetricParameters):
    radius: int = Field(5, description="Radius")
    filters: Filters = Field(default_factory=Filters, description="Filters")


class ProducerParameters(MetricParameters):
    distance: int = Field(gt=0, description="Distance")
    filters: Filters = Field(default_factory=Filters, description="Filters")
    mode: str = Field(" FAST ", description="Mode")

    @field_validator("mode")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return value.strip().lower()


def test_complete_values_transport_execution_and_identity() -> None:
    seen = []
    schema = pa.DataFrameSchema({}, strict=True)

    @metric(
        cache="full",
        code_revision="1",
        name="producer",
        description="Producer",
        outputs={"data": schema},
    )
    def producer(parameters: ProducerParameters) -> pd.DataFrame:
        seen.append(parameters.model_dump())
        parameters.filters.tags.append("handler mutation")
        return pd.DataFrame()

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"data": schema},
        inputs={
            "first": Dependency(
                "data",
                producer="producer",
                parameters={"distance": Input("parameters.radius")},
            ),
            "second": Dependency(
                "data",
                producer="producer",
                parameters={"distance": 9, "filters": {"tags": ["override"]}},
            ),
        },
    )
    def root(
        parameters: RootParameters, first: pd.DataFrame, second: pd.DataFrame
    ) -> pd.DataFrame:
        assert parameters.filters.tags == []
        pd.testing.assert_frame_equal(first, second)
        return first

    catalog = compile_live_catalog({"test": PluginDefinition(metrics=[root, producer])})
    plan = prepare_execution(catalog, {"metric": "root"})
    explicit = prepare_execution(
        catalog,
        {
            "metric": "root",
            "parameters": {"radius": 5, "filters": {"tags": [], "label": None}},
        },
    )
    assert plan == explicit
    assert seen == []
    values = plan.parameter_uses
    assert values[use_key("root", plan.parameters, "inputs.first")] == {
        "distance": 5,
        "filters": {"tags": [], "label": None},
        "mode": "fast",
    }
    assert values[use_key("root", plan.parameters, "inputs.second")] == {
        "distance": 9,
        "filters": {"tags": ["override"], "label": None},
        "mode": "fast",
    }
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    restored = prepare_execution(
        catalog, restored.request.model_dump(), normalized_uses=restored.parameter_uses
    )
    assert restored.parameter_uses == plan.parameter_uses
    executor = GraphExecutor(catalog, catalog.implementations, Mock())
    outcome = executor.execute(restored)
    assert [item["distance"] for item in seen] == [5, 9]
    assert plan.parameter_uses == values
    observations = outcome.provenance.execution.observations
    identities = {item.invocation for item in observations if item.node == "producer"}
    assert len(identities) == 2
    repeated = executor.execute(explicit)
    assert {
        item.invocation
        for item in repeated.provenance.execution.observations
        if item.node == "producer"
    } == identities
    with pytest.raises(PreparationError, match="distance"):
        prepare_execution(catalog, {"metric": "root", "parameters": {"radius": 0}})


def test_nested_operations_keep_declaring_scope() -> None:
    catalog, _ = implementation()
    uses = prepare_parameter_uses(catalog, "root", {"year": 2024}).uses
    root = uses["root"]
    assert uses[use_key("root", root, "inputs.first")]["year"] == 1990
    assert uses[use_key("root", root, "inputs.first.locations.source")]["year"] == 1980
    assert (
        uses[use_key("root", root, "inputs.first.locations.source.locations.source")][
            "year"
        ]
        == 2024
    )


def test_automatic_route_parameters_are_retained() -> None:
    catalog, request = routed_request()
    plan = prepare_execution(catalog, request)
    assert plan.parameter_uses[route_key("places", 0)] == {"count": 2}
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert (
        prepare_execution(
            catalog,
            restored.request.model_dump(),
            normalized_uses=restored.parameter_uses,
        )
        == plan
    )
    del restored.parameter_uses[route_key("places", 0)]["count"]
    with pytest.raises(PreparationError, match="Missing normalized"):
        prepare_execution(
            catalog,
            restored.request.model_dump(),
            normalized_uses=restored.parameter_uses,
        )


def test_execution_uses_accepted_factory_values() -> None:
    accepting = True

    def defaults() -> list[str]:
        # Accepted defaults must travel explicitly, including to dependency uses.
        assert accepting, "Execution reevaluated an accepted default"
        return ["accepted"]

    class Parameters(MetricParameters):
        tags: list[str] = Field(default_factory=defaults, description="Tags")

    @metric(
        cache="full",
        code_revision="1",
        name="leaf",
        description="Leaf",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def leaf(parameters: Parameters) -> pd.DataFrame:
        assert parameters.tags == ["accepted"]
        return pd.DataFrame()

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
        inputs={"data": Dependency("data", producer="leaf")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    catalog = compile_live_catalog({"test": PluginDefinition(metrics=[root, leaf])})
    plan = prepare_execution(catalog, {"metric": "root"})
    accepting = False
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    restored = prepare_execution(
        catalog, restored.request.model_dump(), normalized_uses=restored.parameter_uses
    )
    GraphExecutor(catalog, catalog.implementations, Mock()).execute(restored)


@pytest.mark.usefixtures("dummy_redis_clients")
@pytest.mark.parametrize("explicit", [False, True])
def test_live_defaults_survive_submission_and_worker(
    tmp_path: "Path", monkeypatch: pytest.MonkeyPatch, *, explicit: bool
) -> None:
    accepting = True
    seen = []

    def defaults() -> list[str]:
        assert accepting, "Worker reevaluated an accepted default"
        return [" north "]

    class Filters(MetricParameters):
        tags: list[str] = Field(default_factory=defaults, description="Tags")
        label: str | None = Field(None, description="Label")

        @model_validator(mode="after")
        def normalize(self) -> Self:
            self.tags = [tag.strip() for tag in self.tags]
            return self

    class Parameters(MetricParameters):
        filters: Filters = Field(default_factory=Filters, description="Filters")

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        seen.append(parameters.model_dump(mode="json"))
        parameters.filters.tags.append("handler mutation")
        return pd.DataFrame({"value": [len(parameters.filters.tags)]})

    plugins, runtime = install(tmp_path, monkeypatch, [root])
    runtime.config.mcp.enabled = True
    native = native_job(monkeypatch)
    enqueue = Mock()
    effective: JsonObject = {"filters": {"tags": ["north"], "label": None}}
    request: JsonObject = {"metric": "root"}
    if explicit:
        request["parameters"] = effective
    try:
        rest, wire = asyncio.run(
            submit_both(
                main.create_app(runtime.config, credentials=api_credentials()),
                request,
                enqueue,
            )
        )
        assert rest.status_code == 202
        assert wire["isError"] is False
        assert seen == []
        local = LocalRunner.from_definitions(
            {p.distribution: p.definition for p in plugins}
        )
        assert local.run("root", parameters=effective).tables["answer"][
            "value"
        ].tolist() == [2]
        seen.clear()
        accepting = False
        plans = []
        for call in enqueue.call_args_list:
            envelope = call.args[0]
            native.id = envelope.job_id
            payload = json.loads(envelope.model_dump_json())
            plans.append(payload["accepted"]["plan"])
            assert envelope.accepted.plan.parameters == effective
            result = run_and_read(worker.run_metric_task, payload)
            assert result["outputs"] == {"answer": [{"value": 2}]}
            assert native.meta["execution"]["succeeded"]
            assert envelope.accepted.plan.parameters == effective
        assert len(plans) == 2
        assert plans[0] == plans[1]
        # Equivalent HTTP/MCP requests share the computation cache.
        assert seen == [effective]
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)


def test_distinct_defaulted_uses_keep_their_accepted_values_during_execution() -> None:
    values = iter((3, 7))

    class Parameters(MetricParameters):
        value: int = Field(default_factory=lambda: next(values), description="Value")

    @metric(
        name="leaf",
        code_revision="1",
        description="Use the accepted value",
        outputs={"answer": VALUES},
    )
    def leaf(parameters: Parameters) -> pd.DataFrame:
        return pd.DataFrame({"value": [parameters.value]})

    @metric(
        name="root",
        code_revision="1",
        description="Combine distinct uses",
        inputs={
            "left": Dependency("answer", producer="leaf"),
            "right": Dependency("answer", producer="leaf"),
        },
        outputs={"answer": VALUES},
    )
    def root(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        return left + right

    catalog = compile_live_catalog({"test": PluginDefinition(metrics=[root, leaf])})
    accepted = prepare_execution(catalog, {"metric": "root"})
    assert accepted.parameter_uses[use_key("root", {}, "inputs.left")] == {"value": 3}
    assert accepted.parameter_uses[use_key("root", {}, "inputs.right")] == {"value": 7}
    restored = PreparedExecution.model_validate_json(accepted.model_dump_json())
    output = GraphExecutor(catalog, catalog.implementations, Mock()).execute(restored)
    assert output.outputs["answer"].value.tolist() == [10]
