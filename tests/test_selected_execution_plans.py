"""Selected execution ordering is checked before enqueueing or acquisition."""

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from graphlib import TopologicalSorter
from itertools import starmap
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from lyra.contracts.accepted import PreparedInput
from lyra.contracts.dependencies import Dependency, LocationDependency
from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.locations import LocationContract
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    NodeManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog, assemble_catalog
from lyra.runtime.planning.dependency_routes import compile_dependency_routes
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.server.registry import Catalog
from lyra.server.routes.jobs import create_job
from test_support.location_helpers import node_contract
from test_support.manifest_catalog import compile_catalog


def contract(name: str) -> LocationContract:
    result = node_contract()
    return result.model_copy(
        update={
            "family": result.family.model_copy(update={"collection": f"test:{name}"})
        }
    )


def metric(name: str, **locations: LocationContract) -> MetricManifest:
    return MetricManifest(
        code_revision="1",
        name=name,
        description=name,
        location_inputs={
            key: LocationInputDeclaration(contract=value)
            for key, value in locations.items()
        },
        outputs={name: OutputMetadata(columns=[])},
    )


def operation(
    name: str, source: LocationContract | None, target: LocationContract
) -> LocationOperationManifest:
    return LocationOperationManifest(
        code_revision="1",
        name=name,
        description=name,
        canonical=source is not None,
        location_inputs={"source": LocationInputDeclaration(contract=source)}
        if source
        else {},
        output_contract=target,
    )


def compile_nodes(*nodes: NodeManifest) -> CompiledCatalog:
    return compile_catalog(
        {
            "test": PluginManifest(
                plugin=PluginInfo(name="test", version="1"),
                factory="test:create",
                nodes=list(nodes),
            )
        }
    )


def request(**inputs: LocationContract) -> dict[str, Any]:
    return {
        "metric": "root",
        "inputs": {
            name: {
                "provider": value.family.collection,
                "ids": ["one"],
            }
            for name, value in inputs.items()
        },
    }


def interacting_catalog(
    *, shared_target: bool = False
) -> tuple[CompiledCatalog, list[LocationContract]]:
    """Four conversion edges with acyclic explicit cross-route dependencies."""
    contracts = [contract(name) for name in ("a", "b", "c", "d")]
    a, b, c, d = contracts
    target_c = target_a = contract("target")
    if not shared_target:
        target_c, target_a = c, a
    operations = list(
        starmap(
            operation,
            (("ab", a, b), ("bc", b, target_c), ("cd", c, d), ("da", d, target_a)),
        )
    )
    extra: list[NodeManifest] = []
    for owner, dependency, source, target in (
        (operations[0], "da", d, target_a),
        (operations[2], "bc", b, target_c),
    ):
        table = metric(f"table_{dependency}", places=target)
        table.private = True
        helper = operation(f"supply_{dependency}", None, source)
        owner.inputs = {
            "data": Dependency(
                producer=table.name,
                output=table.name,
            ).bind(
                places=LocationDependency(
                    operation=dependency,
                ).bind(source=LocationDependency(operation=helper.name))
            )
        }
        extra.extend((table, helper))
    root = metric("root", left=target_c, right=target_a)
    return compile_nodes(root, *operations, *extra), contracts


def test_interacting_routes_and_exclusive_alternatives() -> None:
    catalog, (a, _, c, _) = interacting_catalog()
    # Each route is safe alone; their combined ordering closes a cycle.
    assert prepare_execution(catalog, request(left=a, right=a))
    assert prepare_execution(catalog, request(left=c, right=c))
    with pytest.raises(
        PreparationError, match=r"Execution plan cycle.*root.*left.*right"
    ):
        prepare_execution(catalog, request(left=a, right=c))
    assert prepare_execution(catalog, request(left=c, right=a))


def test_interacting_routes_below_the_root_are_validated_together() -> None:
    catalog, (a, _, c, _) = interacting_catalog()
    wrapper = metric("wrapper", left=c, right=a)
    wrapper.inputs = {"data": Dependency("root", producer="root")}
    nested = compile_nodes(
        wrapper, *(entry.node for entry in catalog.graph.nodes.values())
    )
    # The legacy description fixture requires identical input contracts. Exercise
    # the selected-plan boundary after assigning the receiving route requirements.
    nested.graph.nodes["wrapper"].node.location_inputs["left"].contract = a
    nested.graph.nodes["wrapper"].node.location_inputs["right"].contract = c
    nested = assemble_catalog(nested.graph, nested.manifests)
    nested = replace(
        nested,
        dependency_routes=compile_dependency_routes(nested.graph, nested.routes),
    )
    with pytest.raises(
        PreparationError, match=r"Execution plan cycle.*root.*left.*right"
    ):
        prepare_execution(nested, request(left=a, right=c) | {"metric": "wrapper"})


def test_mutually_exclusive_routes_are_not_combined() -> None:
    catalog, (a, _, c, _) = interacting_catalog(shared_target=True)
    native = catalog.graph.nodes["root"].node.location_inputs["left"].contract
    root = metric("root", left=native)
    manifests = dict(catalog.manifests)
    manifests["test"].nodes = [
        root,
        *(n for n in manifests["test"].nodes if n.name != "root"),
    ]
    single = compile_catalog(manifests)
    for source in (a, c, native):
        assert prepare_execution(single, request(left=source))


def test_submission_rejects_cycle_without_enqueueing() -> None:
    catalog, (a, _, c, _) = interacting_catalog()
    jobs = Mock()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_job(
                JobCreateRequest(
                    metric="root",
                    inputs={
                        k: v
                        for k, v in request(left=a, right=c)["inputs"].items()
                        if k != "parameters"
                    },
                    parameters=request(left=a, right=c)["inputs"].get("parameters", {}),
                ),
                catalog=Catalog(
                    catalog,
                    {
                        name: "default"
                        for name, entry in catalog.graph.nodes.items()
                        if entry.node.kind == "metric"
                    },
                ),
                jobs=jobs,
            )
        )
    assert error.value.status_code == 422
    assert "Execution plan cycle" in str(error.value.detail)
    jobs.enqueue.assert_not_called()


def test_local_rejection_precedes_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog, (a, _, c, _) = interacting_catalog()
    runner = LocalRunner.__new__(LocalRunner)
    runner.catalog = catalog
    execute = Mock(side_effect=AssertionError("execution started"))
    monkeypatch.setattr(LocalRunner, "_execute", execute)
    with pytest.raises(PreparationError, match="Execution plan cycle"):
        runner.run("root", inputs=request(left=a, right=c)["inputs"])
    execute.assert_not_called()


@pytest.mark.parametrize("boundary", ["validate_plan", "acquire_sources", "execute"])
def test_captured_cycle_rejected_before_resources(
    boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, (a, _, c, _) = interacting_catalog()
    valid = prepare_execution(catalog, request(left=c, right=a))
    # Exercise the executor's defensive boundary with an otherwise bypassed plan.
    inputs = tuple(
        PreparedInput(
            name=name,
            ids=("one",),
            binding=catalog.accepted_sources["root"][name][value.family.collection],
        )
        for name, value in (("left", a), ("right", c))
    )
    invalid = valid.model_copy(update={"inputs": inputs})
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.runtime.execution.collect_resources", acquire)
    executor = GraphExecutor(catalog, {}, Mock())
    expected = GraphExecutionError if boundary == "execute" else ValueError
    with pytest.raises(expected, match="Execution plan cycle"):
        getattr(executor, boundary)(invalid)
    acquire.assert_not_called()


@pytest.mark.parametrize("input_count", [4, 8])
def test_startup_work_does_not_multiply_input_choices(
    monkeypatch: pytest.MonkeyPatch, input_count: int
) -> None:
    native = contract("native")
    sources = [contract(f"source_{i}") for i in range(3)]
    root = metric("root", **{f"input_{i}": native for i in range(input_count)})
    operations = [
        operation(f"select_{i}", source, native) for i, source in enumerate(sources)
    ]
    original_order = TopologicalSorter.static_order
    visited = 0
    # Startup may inspect individual choices, but must not sort a plan for every
    # combination (4**input_count). Allow a full graph traversal per choice,
    # independent of how those visits are divided into sorting calls.
    budget = (1 + len(operations)) * input_count * (1 + len(sources))

    def static_order(sorter: TopologicalSorter) -> Iterator[str]:
        nonlocal visited
        for node in original_order(sorter):
            visited += 1
            assert visited <= budget, "Startup sorting exceeds per-choice work bound"
            yield node

    with monkeypatch.context() as startup:
        startup.setattr(TopologicalSorter, "static_order", static_order)
        catalog = compile_nodes(root, *operations)
    assert len(catalog.accepted_sources["root"]) == input_count
    assert all(
        len(choices) == 4 for choices in catalog.accepted_sources["root"].values()
    )
    assert prepare_execution(
        catalog, request(**{f"input_{i}": sources[i % 3] for i in range(input_count)})
    )


@pytest.mark.parametrize("missing", ["root", "ab"])
def test_unavailable_captured_node_precedes_acquisition(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, (a, _, _, _) = interacting_catalog()
    prepared = prepare_execution(catalog, request(left=a, right=a))
    del catalog.graph.nodes[missing]
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.runtime.execution.collect_resources", acquire)
    executor = GraphExecutor(catalog, {}, Mock())
    with pytest.raises(ValueError, match=f"unavailable node {missing!r}"):
        executor.acquire_sources(prepared)
    acquire.assert_not_called()
