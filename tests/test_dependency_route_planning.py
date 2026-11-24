"""Receiving routes are part of acceptance before any acquisition or execution."""

from dataclasses import replace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.parameters import receiving_key, route_key
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from tests.dependency_route_helpers import definitions, request
from tests.live_graph_helpers import operation
from tests.node_declaration_helpers import provider


def test_matching_and_explicit_routes_have_equivalent_effective_bindings() -> None:
    roots = []
    routes = []
    for binding in ("matching", "explicit"):
        catalog = compile_live_catalog(definitions(binding=binding))
        plan = prepare_execution(catalog, request())
        routes.append(plan.dependency_routes)
        root = GraphExecutor(catalog, Mock()).validate_plan(plan)
        roots.append(root)
        left, right = root.dependencies["left"], root.dependencies["right"]
        assert left.identity != right.identity
        assert left.parameter_values()["count"] == 5
        assert right.parameter_values()["count"] == 6
        assert left.locations["reference"] == root.locations["reference"]
        assert right.locations["reference"] == root.locations["reference"]
        route = plan.dependency_routes[receiving_key("root", "inputs.left", "places")]
        assert route.operations == ("first", "finish")
        native = plan.dependency_routes[
            receiving_key("root", "inputs.left", "reference")
        ]
        assert native.operations == ()
        assert native.source == native.target
        assert {item.contract.collection for item in plan.revisions.bindings} == {
            "source",
            "middle",
            "target",
            "reference",
        }
        assert {item.family.collection for item in plan.locations} == {
            "source",
            "middle",
            "target",
            "reference",
        }
        assert root.origins == frozenset({Origin(id="dataset", revision="1")})
        for side in ("left", "right"):
            route_use = receiving_key("root", f"inputs.{side}", "places")
            for index in (0, 1):
                assert plan.parameter_uses[route_key(route_use, index)] == {
                    "count": 2,
                    "tags": [],
                }
    assert routes[0] == routes[1]
    assert roots[0].identity == roots[1].identity


def test_nested_operation_results_and_sources_keep_original_caller_scope() -> None:
    catalog = compile_live_catalog(definitions(binding="nested"))
    plan = prepare_execution(catalog, request())
    bound = bind_execution(catalog, plan)
    target = bound.dependencies["left"].locations["places"]
    assert target.provider.collection == "target"
    finish = target.upstream
    assert finish is not None
    assert finish.node == "finish"
    outer = finish.locations["source"].upstream
    assert outer is not None
    assert outer.node == "explicit"
    assert outer.parameter_values()["count"] == 7
    source_route = outer.locations["source"].upstream
    assert source_route is not None
    assert source_route.node == "finish"
    inner = source_route.locations["source"].upstream
    assert inner is not None
    assert inner.node == "initial"
    assert inner.parameter_values()["count"] == 99
    assert inner.locations["source"] == bound.locations["places"]
    assert inner.locations["source"].ids == ("a", "b")
    assert {route.operations for route in plan.dependency_routes.values()} == {
        (),
        ("finish",),
        ("reference_first", "finish"),
    }


@pytest.mark.parametrize(
    "change",
    [
        "first",
        "finish",
        "preference",
        "middle",
        "middle_provider",
        "target_provider",
        "origin",
        "description",
        "unused",
        "alternate",
    ],
)
def test_live_route_changes_invalidate_only_selected_computations(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = compile_live_catalog(definitions(), build_revision="same-build")
    accepted = prepare_execution(original, request())
    restored = PreparedExecution.model_validate_json(accepted.model_dump_json())
    current = compile_live_catalog(
        definitions({change: "alternate" if change == "preference" else "changed"}),
        build_revision="same-build",
    )
    changed = prepare_execution(current, request())
    invalid = change not in {"description", "unused", "alternate"}
    assert (changed.computation_identity != restored.computation_identity) == invalid
    executor = GraphExecutor(current, Mock())
    if invalid:
        # This boundary must fail before acquiring resources or reading cache.
        acquire = Mock(side_effect=AssertionError("acquisition"))
        monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
        executor.store = Mock(side_effect=AssertionError("cache lookup"))
        with pytest.raises(
            GraphExecutionError, match=r"identity differs|routes differ"
        ):
            executor.execute(restored)
        acquire.assert_not_called()
        assert executor.store.mock_calls == []
    else:
        assert (
            executor.validate_plan(restored).identity
            == bind_execution(original, accepted).identity
        )


@pytest.mark.parametrize("damage", ["route", "missing_route", "value", "missing_value"])
def test_serialized_route_acceptance_cannot_change_silently(
    damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = compile_live_catalog(definitions())
    plan = prepare_execution(catalog, request())
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    key = receiving_key("root", "inputs.left", "places")
    if damage == "route":
        restored.dependency_routes[key] = replace(
            restored.dependency_routes[key], operations=("alternate", "finish")
        )
    elif damage == "missing_route":
        del restored.dependency_routes[key]
    elif damage == "value":
        restored.parameter_uses[route_key(key, 0)]["count"] = 17
    else:
        del restored.parameter_uses[route_key(key, 0)]
    acquire = Mock(side_effect=AssertionError("acquisition"))
    executor = GraphExecutor(catalog, Mock())
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    with pytest.raises(
        GraphExecutionError, match=r"routes differ|identity differs|Missing normalized"
    ):
        executor.execute(restored)
    acquire.assert_not_called()
    assert plan.parameter_uses[route_key(key, 0)]["count"] == 2


def test_normalized_route_factories_survive_transport_and_ownership() -> None:
    accepting = True

    def defaults() -> list[str]:
        assert accepting, "Accepted route default was evaluated again"
        return ["accepted"]

    class Parameters(MetricParameters):
        count: int = Field(2, description="Count")
        tags: list[str] = Field(default_factory=defaults, description="Tags")

    catalog = compile_live_catalog(definitions(options=Parameters))
    plan = prepare_execution(catalog, request())
    accepting = False
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    normalized = prepare_execution(
        catalog, restored.request.model_dump(), normalized_uses=restored.parameter_uses
    )
    assert normalized == plan
    root = GraphExecutor(catalog, Mock()).validate_plan(restored)
    left = root.dependencies["left"].locations["places"].upstream
    right = root.dependencies["right"].locations["places"].upstream
    assert left is not None
    assert right is not None
    assert (
        left.parameter_values()
        == right.parameter_values()
        == {"count": 2, "tags": ["accepted"]}
    )
    assert left.identity != right.identity
    left.parameter_values()["tags"].append("mutation")
    key = receiving_key("root", "inputs.left", "places")
    tags = normalized.parameter_uses[route_key(key, 1)]["tags"]
    assert isinstance(tags, list)
    tags.append("mutation")
    assert right.parameter_values()["tags"] == ["accepted"]
    assert plan.parameter_uses[route_key(key, 1)]["tags"] == ["accepted"]


def test_empty_selections_and_unordered_identity_preserve_fixed_scopes() -> None:
    origin = Origin(id="dataset", revision="1")
    roots = []
    for ids, origins in (
        (("a", "b"), (origin, Origin(id="other"))),
        (
            ("b", "a", "a"),
            (
                Origin(id="other"),
                Origin(id="dataset", revision="1", description="Edited"),
                origin,
            ),
        ),
        ((), (origin, Origin(id="other"))),
    ):
        catalog = compile_live_catalog(definitions(origins=origins))
        plan = prepare_execution(catalog, request(ids))
        roots.append(bind_execution(catalog, plan))
    assert roots[0].identity == roots[1].identity
    assert roots[0].identity != roots[2].identity
    assert roots[2].locations["places"].ids == ()
    for root in roots:
        target = root.dependencies["left"].locations["places"]
        assert target.ids is None
        assert (
            target.provider
            == roots[0].dependencies["left"].locations["places"].provider
        )
        assert (
            root.dependencies["left"].locations["reference"]
            == roots[0].locations["reference"]
        )


def test_independent_receiving_uses_may_order_operations_differently() -> None:
    forward = operation("forward", "a", "b")
    reverse = operation("reverse", "b", "a")

    @metric(
        name="root",
        code_revision="1",
        description="Independent operations",
        inputs={
            "a": LocationDependency(provider="a"),
            "b": LocationDependency(provider="b"),
            "left": LocationDependency("forward").bind(source=Input("b")),
            "right": LocationDependency("reverse").bind(source=Input("a")),
        },
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )
    def root(
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        left: gpd.GeoDataFrame,
        right: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        del a, b, left, right
        pytest.fail("Planning invoked a handler")

    catalog = compile_live_catalog(
        {
            "test": PluginDefinition(
                metrics=[root],
                location_operations=[forward, reverse],
                resources={"a": provider(id="a"), "b": provider(id="b")},
            )
        }
    )
    plan = prepare_execution(
        catalog,
        {
            "metric": "root",
            "inputs": {
                "a": {"provider": "a", "ids": []},
                "b": {"provider": "b", "ids": []},
            },
        },
    )
    bound = GraphExecutor(catalog, Mock()).validate_plan(plan)
    assert bound.locations["left"].provider.collection == "b"
    assert bound.locations["right"].provider.collection == "a"
    assert plan.dependency_routes[
        receiving_key("root", "inputs.left", "source")
    ].operations == ("reverse",)
    assert plan.dependency_routes[
        receiving_key("root", "inputs.right", "source")
    ].operations == ("forward",)


def test_routes_below_repeated_producer_uses_have_independent_defaults() -> None:
    accepting = True
    generated = []

    def defaults() -> list[int]:
        assert accepting, "Preflight reran a route factory"
        generated.append(len(generated))
        return [generated[-1]]

    class Parameters(MetricParameters):
        tags: list[int] = Field(default_factory=defaults, description="Tags")

    plugins = definitions(options=Parameters)
    table = pa.DataFrameSchema(strict=True)

    @metric(
        name="middle_metric",
        code_revision="1",
        description="Forward a source",
        inputs={
            "places": LocationDependency(provider="source"),
            "reference": LocationDependency(provider="reference"),
            "data": Dependency("value", producer="producer"),
        },
        outputs={"value": table},
    )
    def middle_metric(
        places: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, data: pd.DataFrame
    ) -> pd.DataFrame:
        del places, reference, data
        pytest.fail("Planning invoked a handler")

    @metric(
        name="outer",
        code_revision="1",
        description="Two uses with equal parameters",
        inputs={
            "places": LocationDependency(provider="source"),
            "reference": LocationDependency(provider="reference"),
            "left": Dependency("value", producer="middle_metric"),
            "right": Dependency("value", producer="middle_metric"),
        },
        outputs={"value": table},
    )
    def outer(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        left: pd.DataFrame,
        right: pd.DataFrame,
    ) -> pd.DataFrame:
        del places, reference, left, right
        pytest.fail("Planning invoked a handler")

    plugins["outer"] = PluginDefinition(metrics=[outer, middle_metric])
    catalog = compile_live_catalog(plugins)
    plan = prepare_execution(catalog, request() | {"metric": "outer", "parameters": {}})
    accepting = False
    bound = GraphExecutor(catalog, Mock()).validate_plan(plan)
    left = bound.dependencies["left"].dependencies["data"].locations["places"].upstream
    right = (
        bound.dependencies["right"].dependencies["data"].locations["places"].upstream
    )
    assert left is not None
    assert right is not None
    assert left.parameter_values() != right.parameter_values()
    assert left.identity != right.identity
    assert left.parameter_use_key != right.parameter_use_key
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert bind_execution(catalog, restored).identity == bound.identity
