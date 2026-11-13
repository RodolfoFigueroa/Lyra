"""Mutable inputs and inspection views cannot change retained execution state."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
from pydantic import Field, JsonValue

from lyra.contracts.accepted import SubmittedRequest
from lyra.contracts.jobs import AcceptedJob, JobEnvelope, JobRunProvenance
from lyra.contracts.locations import LocationSelection
from lyra.contracts.plugins import PluginInfo
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric


class Nested(MetricParameters):
    tags: list[str] = Field(default_factory=list, description="Tags")


class Parameters(MetricParameters):
    nested: Nested = Field(default_factory=Nested, description="Nested values")
    options: dict[str, JsonValue] = Field(default_factory=dict, description="Options")


def test_shared_constructor_values_and_handler_arguments() -> None:
    shared = {"tags": ["original"]}
    first = Dependency("data", producer="source", parameters={"nested": shared})
    second = Dependency("data", producer="source", parameters={"nested": shared})
    operation = LocationDependency("operation", parameters={"nested": shared})
    outer = LocationDependency("outer").bind(source=operation)
    shared["tags"].append("caller")
    first.parameters.clear()
    operation.parameters.clear()
    assert second.parameters == {"nested": {"tags": ["original"]}}
    captured = outer.locations["source"]
    assert isinstance(captured, LocationDependency)
    assert captured.parameters == second.parameters

    parameters = Parameters(nested=Nested(tags=["original"]), options={"values": [1]})
    invocations = [PreparedResolvedInvocation("node", parameters, {}) for _ in range(2)]
    identities = [item.identity for item in invocations]
    parameters.nested.tags.clear()
    parameters.options.clear()
    for invocation in invocations:
        for _ in range(2):
            exported = invocation.arguments()["parameters"]
            assert isinstance(exported, Parameters)
            assert exported.nested.tags == ["original"]
            assert exported.options == {"values": [1]}
            exported.nested.tags.clear()
            exported.options.clear()
        assert invocation.inspect_parameters() == Parameters(
            nested=Nested(tags=["original"]), options={"values": [1]}
        )
    assert [item.identity for item in invocations] == identities


def test_registration_execution_and_inspection_are_independent() -> None:
    seen = []
    binding = Dependency(
        "data", producer="leaf", parameters={"nested": {"tags": ["bound"]}}
    )

    @metric(
        code_revision="1",
        name="leaf",
        description="Leaf",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def leaf(parameters: Parameters) -> pd.DataFrame:
        seen.append(parameters.model_dump())
        parameters.nested.tags.clear()
        parameters.options["changed"] = True
        return pd.DataFrame()

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
        inputs={"data": binding},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    definition = PluginDefinition(metrics=[leaf, root])
    nested_binding = binding.parameters["nested"]
    assert isinstance(nested_binding, dict)
    nested_binding["tags"].append("author mutation")
    catalog = compile_live_catalog({"test": definition})
    discovery = Discovery(catalog)
    description = discovery.inspect("root")
    description.outputs.clear()
    assert discovery.inspect("root").outputs
    plan = prepare_execution(catalog, {"metric": "root"})
    original = plan.model_dump()
    executor = GraphExecutor(catalog, catalog.implementations, Mock())
    outcome = executor.execute(plan)
    assert outcome.provenance.accepted is not None
    outcome.provenance.accepted.parameter_uses.clear()
    retained = executor.provenance
    assert retained is not None
    assert retained.accepted is not None
    assert retained.accepted.model_dump() == original
    retained.accepted.outputs.clear()
    executor.execute(plan)
    assert plan.model_dump() == original
    runner = LocalRunner.from_definitions({"test": definition})
    runner.run("root")
    inspection = runner.inspect()
    assert inspection is not None
    assert inspection.accepted is not None
    inspection.accepted.request.parameters["changed"] = True
    again = runner.inspect()
    assert again is not None
    assert again.accepted is not None
    assert again.accepted.request.parameters == {}
    runner.run("root")
    assert seen == [{"nested": {"tags": ["bound"]}, "options": {}}] * 4


def test_job_construction_captures_accepted_metadata() -> None:
    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def root(parameters: Parameters) -> pd.DataFrame:
        return pd.DataFrame({"tags": parameters.nested.tags})

    catalog = compile_live_catalog({"test": PluginDefinition(metrics=[root])})
    raw = {"metric": "root", "parameters": {"nested": {"tags": ["accepted"]}}}
    plan = prepare_execution(catalog, raw)
    accepted = AcceptedJob(
        plan=plan,
        plugin=PluginInfo(name="test", version="1"),
        catalog_fingerprint="catalog",
        created_at=datetime.now(UTC),
    )
    envelope = JobEnvelope(job_id="job", accepted=accepted)
    provenance = JobRunProvenance(accepted=accepted)
    expected = envelope.model_dump()
    raw["parameters"].clear()
    plan.parameters.clear()
    accepted.plan.request.parameters.clear()
    provenance.accepted.plan.outputs.clear()
    assert envelope.model_dump() == expected
    assert envelope.accepted.plan.parameters["nested"] == {"tags": ["accepted"]}
    outputs = envelope.accepted.plan.output_metadata
    outputs.clear()
    assert envelope.accepted.plan.output_metadata


def test_accepted_request_owns_nested_constructor_inputs() -> None:
    selection = LocationSelection(provider="city_places", ids=["one"])
    parameters = {"options": {"values": [1]}}
    first = SubmittedRequest(
        metric="root", parameters=parameters, inputs={"places": selection}
    )
    second = SubmittedRequest(
        metric="root", parameters=parameters, inputs={"places": selection}
    )
    selection.ids.append("two")
    parameters["options"]["values"].append(2)
    first.inputs["places"].ids.clear()
    first.parameters.clear()
    assert second.inputs["places"].ids == ["one"]
    assert second.parameters == {"options": {"values": [1]}}
