"""Selected execution ordering is checked before enqueueing or acquisition."""

import asyncio
from collections.abc import Iterator
from graphlib import TopologicalSorter
from itertools import starmap
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from lyra.sdk import LocalRunner
from lyra.sdk.accepted import PreparedInput
from lyra.sdk.catalog import CompiledCatalog, compile_catalog
from lyra.sdk.dependencies import DependencyManifest, LocationDependencyManifest
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    NodeManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.sdk.preparation import PreparationError, prepare_execution

from lyra_app.registry import Catalog
from lyra_app.routes.jobs import create_job
from tests.location_helpers import node_contract


def contract(name: str) -> LocationContract:
    result = node_contract()
    return result.model_copy(
        update={
            "family": result.family.model_copy(update={"collection": f"test:{name}"})
        }
    )


def metric(name: str, **locations: LocationContract) -> MetricManifest:
    return MetricManifest(
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
                "collection": value.family.collection,
                "unit_type": value.family.unit_type,
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
            "data": DependencyManifest(
                producer=table.name,
                output=table.name,
                locations={
                    "places": LocationDependencyManifest(
                        operation=dependency,
                        locations={
                            "source": LocationDependencyManifest(operation=helper.name)
                        },
                    )
                },
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
                    metric="root", input=request(left=a, right=c)["inputs"]
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
        runner.run("root", request(left=a, right=c)["inputs"])
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
            binding=catalog.accepted_sources["root"][name][
                value.family.collection, value.family.unit_type
            ],
        )
        for name, value in (("left", a), ("right", c))
    )
    invalid = valid.model_copy(update={"inputs": inputs})
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.sdk.execution.collect_resources", acquire)
    executor = GraphExecutor(catalog, {}, Mock())
    expected = GraphExecutionError if boundary == "execute" else ValueError
    with pytest.raises(expected, match="Execution plan cycle"):
        getattr(executor, boundary)(invalid)
    acquire.assert_not_called()


def test_startup_work_does_not_multiply_input_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = contract("native")
    sources = [contract(f"source_{i}") for i in range(3)]
    root = metric("root", **{f"input_{i}": native for i in range(8)})
    operations = [
        operation(f"select_{i}", source, native) for i, source in enumerate(sources)
    ]
    sorts = Mock(wraps=TopologicalSorter.static_order)
    # Count actual sorting work, independent of which module requests it.

    def static_order(sorter: TopologicalSorter) -> Iterator[str]:
        return sorts(sorter)

    monkeypatch.setattr(TopologicalSorter, "static_order", static_order)
    catalog = compile_nodes(root, *operations)
    assert all(
        len(choices) == 4 for choices in catalog.accepted_sources["root"].values()
    )
    startup_sorts = sorts.call_count
    assert startup_sorts <= 2
    prepare_execution(
        catalog, request(**{f"input_{i}": sources[i % 3] for i in range(8)})
    )
    assert sorts.call_count == startup_sorts + 1


@pytest.mark.parametrize("missing", ["root", "ab"])
def test_unavailable_captured_node_precedes_acquisition(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, (a, _, _, _) = interacting_catalog()
    prepared = prepare_execution(catalog, request(left=a, right=a))
    del catalog.graph.nodes[missing]
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.sdk.execution.collect_resources", acquire)
    executor = GraphExecutor(catalog, {}, Mock())
    with pytest.raises(ValueError, match=f"unavailable node {missing!r}"):
        executor.acquire_sources(prepared)
    acquire.assert_not_called()
