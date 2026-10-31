"""Mixed invocation preparation without executing graph handlers."""

from dataclasses import replace

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    Locations,
    MetricInputError,
    PluginDefinition,
    content_fingerprints,
    location_operation,
    metric,
)
from lyra.sdk.arguments import (
    InvocationPreparer,
    PreparedInvocation,
    PreparedResolvedInvocation,
)
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.location_snapshot import LocationSnapshot

from tests.location_helpers import fixture_locations, output_schema, producer_result
from tests.test_graph_arguments import Producer
from tests.test_location_schema import column, contract, declaration, locations


def implementation() -> tuple[PluginGraph, dict[str, PluginDefinition]]:
    output = contract(declaration(count=column(int, coerce=True)))

    @location_operation(
        name="inner", description="Inner", output=output, scope_from=Input("source")
    )
    def inner(source: Locations, parameters: Producer) -> gpd.GeoDataFrame:
        pytest.fail(f"Unexpected handler execution: {source}, {parameters}")

    @location_operation(
        name="outer",
        description="Outer",
        output=output,
        inputs={"table": Dependency("leaf")},
        scope_from=Input("source"),
    )
    def outer(
        source: Locations, parameters: Producer, table: pd.DataFrame
    ) -> gpd.GeoDataFrame:
        pytest.fail(f"Unexpected handler execution: {source}, {parameters}, {table}")

    @metric(name="leaf", description="Leaf", outputs={"leaf": pa.DataFrameSchema({})})
    def leaf(source: Locations, parameters: Producer) -> pd.DataFrame:
        pytest.fail(f"Unexpected handler execution: {source}, {parameters}")

    @metric(
        name="middle",
        description="Middle",
        outputs={"middle": pa.DataFrameSchema({})},
        inputs={"table": Dependency("leaf")},
    )
    def middle(
        source: Locations, parameters: Producer, table: pd.DataFrame
    ) -> pd.DataFrame:
        pytest.fail(f"Unexpected handler execution: {source}, {parameters}, {table}")

    binding = LocationDependency(
        "outer",
        parameters={"year": 1980},
        locations={
            "source": LocationDependency(
                "inner",
                locations={"source": Input("alias")},
                parameters={"year": Input("parameters.year")},
            )
        },
    )

    @metric(
        name="root",
        description="Root",
        outputs={"root": pa.DataFrameSchema({})},
        inputs={
            "first": Dependency(
                "middle",
                parameters={"year": 1990},
                locations={"source": binding},
            ),
            "second": Dependency(
                "middle",
                parameters={"year": 1990, "count": 2},
                locations={"source": binding},
            ),
        },
    )
    def root(
        source: Locations,
        alias: Locations,
        parameters: Producer,
        first: pd.DataFrame,
        second: pd.DataFrame,
    ) -> pd.DataFrame:
        pytest.fail(
            f"Unexpected handler execution: {source}, {alias}, "
            f"{parameters}, {first}, {second}"
        )

    plugins = {
        "root": PluginDefinition(metrics=[root]),
        "tables": PluginDefinition(metrics=[middle, leaf]),
        "operations": PluginDefinition(location_operations=[inner, outer]),
    }
    graph = build_graph({name: p.node_manifests for name, p in plugins.items()})
    return graph, plugins


def test_nested_owner_forwarding_defaults_and_normalized_identity() -> None:
    graph, plugins = implementation()
    seen: list[PreparedResolvedInvocation] = []
    raw = locations({"count": "3"})

    def evaluate(invocation: PreparedResolvedInvocation) -> object:
        seen.append(invocation)
        return producer_result(
            raw, contract(declaration(count=column(int, coerce=True)))
        )

    preparer = InvocationPreparer(graph, plugins, evaluate)
    original = locations({"count": 1})
    alias = locations({"count": 2})
    owner = plugins["root"].prepare_resolved_invocation(
        "root", {"source": original, "alias": alias, "parameters": {"year": 2024}}
    )
    result = preparer.prepare(owner, graph.dependencies["root"]["first"])
    inner, outer = seen
    assert inner.kind == outer.kind == "location_operation"
    assert inner.parameters == Producer(year=2024)
    assert inner.locations["source"] == alias
    assert outer.parameters == Producer(year=1980)
    assert outer.locations["source"] == locations({"count": 3})
    assert result.parameters == Producer(year=1990)
    assert result.locations["source"] == locations({"count": 3})

    # Dependencies authored by a target use that target's effective inputs.
    for target in (outer, result):
        leaf = preparer.prepare(target, graph.dependencies[target.node]["table"])
        assert leaf.parameters == target.parameters
        assert leaf.locations["source"] == target.locations["source"]

    equivalent = preparer.prepare(owner, graph.dependencies["root"]["second"])
    assert equivalent.identity == result.identity
    assert [value.identity for value in seen[:2]] == [
        value.identity for value in seen[2:]
    ]
    direct = plugins["tables"].prepare_resolved_invocation(
        "middle", {"source": locations({"count": 3}), "parameters": {"year": 1990}}
    )
    assert direct.identity == result.identity
    # Raw operation representations are not consumer identity inputs.
    raw = locations({"count": 3})
    assert preparer.prepare(owner, graph.dependencies["root"]["first"]).identity == (
        result.identity
    )


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        producer_result(
            locations({"count": "invalid"}),
            contract(declaration(count=column(int, coerce=True))),
        ),
        producer_result(
            locations({"unexpected": 2}),
            contract(declaration(count=column(int, coerce=True))),
        ),
    ],
)
def test_invalid_operation_result_prevents_consumer_preparation(
    invalid: object,
) -> None:
    graph, plugins = implementation()
    preparer = InvocationPreparer(graph, plugins, lambda _: invalid)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations({"count": 1}), "alias": locations(), "parameters": {}},
    )
    with pytest.raises(MetricInputError, match="inner") as error:
        preparer.prepare(owner, graph.dependencies["root"]["first"])
    assert "inputs.first.locations.source.locations.source" in str(error.value)
    assert "producer 'middle'" in str(error.value)


def test_invalid_operation_parameters_fail_before_evaluation() -> None:
    graph, plugins = implementation()
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations(), "alias": locations(), "parameters": {}},
    )
    # Models can be copied without validation; target validation must still run.
    owner = replace(owner, parameters=Producer().model_copy(update={"year": "bad"}))

    def forbidden(_: PreparedResolvedInvocation) -> object:
        pytest.fail("Invalid operation was evaluated")

    with pytest.raises(MetricInputError, match=r"parameters\.year"):
        InvocationPreparer(graph, plugins, forbidden).prepare(
            owner, graph.dependencies["root"]["first"]
        )


def test_identity_kind_receiving_names_feature_order_and_mapping_order() -> None:
    value = locations({"count": 1}, {"count": 2})
    first = PreparedResolvedInvocation("node", Producer(), {"source": value})
    assert first.identity != replace(first, kind="location_operation").identity
    assert first.identity != replace(first, locations={"alias": value}).identity
    reversed_value = fixture_locations(
        value.to_dict() | {"features": list(reversed(value.to_features()))},
        schema=output_schema(count=int),
    )
    assert (
        first.identity != replace(first, locations={"source": reversed_value}).identity
    )
    assert (
        replace(first, locations={"a": value, "b": value}).identity
        == replace(first, locations={"b": value, "a": value}).identity
    )


@pytest.mark.parametrize("resolved", [False, True])
def test_preparation_revalidates_malformed_location_instances(
    *, resolved: bool
) -> None:
    _, plugins = implementation()
    prepare = (
        plugins["tables"].prepare_resolved_invocation
        if resolved
        else plugins["tables"].prepare_invocation
    )
    malformed = object.__new__(Locations)
    with pytest.raises(MetricInputError, match="source"):
        prepare("leaf", {"source": malformed, "parameters": {}})


def test_plugin_preparation_includes_operation_kind_and_effective_defaults() -> None:
    _, plugins = implementation()
    plugin = plugins["operations"]
    value = locations()
    for prepare in (plugin.prepare_invocation, plugin.prepare_resolved_invocation):
        omitted = prepare("inner", {"source": value, "parameters": {}})
        explicit = prepare(
            "inner", {"source": value, "parameters": {"year": 2000, "count": 2}}
        )
        assert omitted.kind == "location_operation"
        assert omitted.identity == explicit.identity
        assert omitted.identity != replace(omitted, kind="metric").identity


@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("field", ["crs", "features"])
def test_preparation_rejects_unvalidated_nested_location_models(
    *, resolved: bool, field: str
) -> None:
    _, plugins = implementation()
    prepare = (
        plugins["tables"].prepare_resolved_invocation
        if resolved
        else plugins["tables"].prepare_invocation
    )
    value = locations({"count": 1})
    payload = value.to_dict()
    malformed = payload | {field: payload[field]}
    with pytest.raises(MetricInputError, match="source"):
        prepare("leaf", {"source": malformed, "parameters": {}})

    # Explicit imports produce usable runtime values.
    prepared = prepare(
        "leaf",
        {
            "source": fixture_locations(payload, schema=output_schema(count=int)),
            "parameters": {},
        },
    )
    source = prepared.arguments()["source"]
    assert isinstance(source, Locations)
    assert source.crs.properties.name == value.crs.properties.name
    assert source.ids == value.ids


@pytest.mark.parametrize("resolved", [False, True])
def test_identity_owns_callers_and_uses_cached_content(
    monkeypatch: pytest.MonkeyPatch, *, resolved: bool
) -> None:

    value = locations({"count": 1})
    equal = locations({"count": 1})
    parameters = Producer()
    inputs = {"source": value}
    cls = PreparedResolvedInvocation if resolved else PreparedInvocation
    prepared = cls("node", parameters, inputs)
    expected = prepared.identity
    assert cls("node", Producer(), {"source": equal}).identity == expected
    parameters.year += 1
    inputs.clear()
    assert prepared.identity == expected

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Identity must reuse the snapshot fingerprint")

    monkeypatch.setattr(Locations, "to_dict", forbidden)
    monkeypatch.setattr(LocationSnapshot, "to_dict", forbidden)
    monkeypatch.setattr(content_fingerprints, "typed_snapshot", forbidden)
    for _ in range(3):
        assert prepared.identity == expected
        assert cls("node", Producer(), {"source": equal}).identity == expected
    effective = prepared.parameters
    assert isinstance(effective, Producer)
    effective.year += 1
    assert prepared.identity != expected
    exported = prepared.arguments()["parameters"]
    assert exported == effective
    assert exported is not effective
