"""Mixed bindings, ownership, and preparation through graph execution."""

from dataclasses import replace
from typing import cast
from unittest.mock import Mock

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
    PreparedResolvedInvocation,
)
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.location_snapshot import LocationSnapshot

from tests.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    producer_result,
)
from tests.test_graph_arguments import Producer
from tests.test_location_schema import column, contract, declaration, locations

_DEFAULT = object()


def implementation(
    seen: list[tuple[str, Locations, Producer]] | None = None,
    operation_result: object = _DEFAULT,
) -> tuple[PluginGraph, dict[str, PluginDefinition]]:
    seen = [] if seen is None else seen
    output = contract(declaration(count=column(int, coerce=True)))

    @location_operation(
        locations={"source": node_contract("example:places", "point", output)},
        output_contract=node_contract("example:places", "point", output),
        name="inner",
        description="Inner",
        output=output,
    )
    def inner(source: Locations, parameters: Producer) -> gpd.GeoDataFrame:
        seen.append(("inner", source, parameters))
        return (
            cast("gpd.GeoDataFrame", operation_result)
            if operation_result is not _DEFAULT
            else producer_result(locations({"count": "3"}), output)
        )

    @location_operation(
        locations={"source": node_contract("example:places", "point", output)},
        output_contract=node_contract("example:places", "point", output),
        name="outer",
        description="Outer",
        output=output,
        inputs={"table": Dependency("leaf", producer="leaf")},
    )
    def outer(
        source: Locations, parameters: Producer, table: pd.DataFrame
    ) -> gpd.GeoDataFrame:
        assert table.empty
        seen.append(("outer", source, parameters))
        return source.to_geodataframe()

    @metric(
        locations={"source": node_contract("example:places", "point", output)},
        name="leaf",
        description="Leaf",
        outputs={"leaf": pa.DataFrameSchema({})},
    )
    def leaf(source: Locations, parameters: Producer) -> pd.DataFrame:
        seen.append(("leaf", source, parameters))
        return pd.DataFrame()

    @metric(
        locations={"source": node_contract("example:places", "point", output)},
        name="middle",
        description="Middle",
        outputs={"middle": pa.DataFrameSchema({})},
        inputs={"table": Dependency("leaf", producer="leaf")},
    )
    def middle(
        source: Locations, parameters: Producer, table: pd.DataFrame
    ) -> pd.DataFrame:
        seen.append(("middle", source, parameters))
        return table

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
        locations={
            "source": node_contract("example:places", "point", output),
            "alias": node_contract("example:places", "point", output),
        },
        name="root",
        description="Root",
        outputs={"root": pa.DataFrameSchema({})},
        inputs={
            "first": Dependency(
                "middle",
                parameters={"year": 1990},
                locations={"source": binding},
                producer="middle",
            ),
            "second": Dependency(
                "middle",
                parameters={"year": 1990, "count": 2},
                locations={"source": binding},
                producer="middle",
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
        assert isinstance(alias, Locations)
        seen.append(("root", source, parameters))
        pd.testing.assert_frame_equal(first, second)
        return first

    plugins = {
        "root": PluginDefinition(metrics=[root]),
        "tables": PluginDefinition(metrics=[middle, leaf]),
        "operations": PluginDefinition(location_operations=[inner, outer]),
    }
    graph = build_graph({name: p.node_manifests for name, p in plugins.items()})
    return graph, plugins


def test_nested_owner_forwarding_defaults_and_normalized_identity() -> None:
    seen: list[tuple[str, Locations, Producer]] = []
    graph, plugins = implementation(seen)
    alias = locations({"count": 2})
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {
            "source": locations({"count": 1}),
            "alias": alias,
            "parameters": {"year": 2024},
        },
    )
    executor = GraphExecutor(graph, plugins, Mock())
    executor.execute(owner)
    assert [name for name, _, _ in seen] == [
        "inner",
        "leaf",
        "outer",
        "leaf",
        "middle",
        "root",
    ]
    assert seen[0][1] == alias
    assert seen[0][2] == Producer(year=2024)
    for index, year in [(1, 1980), (2, 1980), (3, 1990), (4, 1990)]:
        assert seen[index][1] == locations({"count": 3})
        assert seen[index][2] == Producer(year=year)
    # Equivalent dependency declarations execute once after normalization.
    assert sum(name == "middle" for name, _, _ in seen) == 1
    assert sum(name == "inner" for name, _, _ in seen) == 1


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
    seen: list[tuple[str, Locations, Producer]] = []
    graph, plugins = implementation(seen, invalid)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations({"count": 1}), "alias": locations(), "parameters": {}},
    )
    with pytest.raises(GraphExecutionError, match="inner") as error:
        GraphExecutor(graph, plugins, Mock()).execute(owner)
    assert error.value.node == "inner"
    assert error.value.path == ("root", "middle", "outer", "inner")
    assert (
        error.value.receiving_input == "inputs.first.locations.source.locations.source"
    )
    assert [name for name, _, _ in seen] == ["inner"]


def test_invalid_operation_parameters_fail_before_evaluation() -> None:
    seen: list[tuple[str, Locations, Producer]] = []
    graph, plugins = implementation(seen)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations(), "alias": locations(), "parameters": {}},
    )
    # Models can be copied without validation; target validation must still run.
    owner = replace(owner, parameters=Producer().model_copy(update={"year": "bad"}))

    with pytest.raises(GraphExecutionError, match=r"parameters\.year"):
        GraphExecutor(graph, plugins, Mock()).execute(owner)
    assert seen == []


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


def test_preparation_revalidates_malformed_location_instances() -> None:
    _, plugins = implementation()
    prepare = plugins["tables"].prepare_resolved_invocation
    malformed = object.__new__(Locations)
    with pytest.raises(MetricInputError, match="source"):
        prepare("leaf", {"source": malformed, "parameters": {}})


def test_plugin_preparation_includes_operation_kind_and_effective_defaults() -> None:
    _, plugins = implementation()
    plugin = plugins["operations"]
    value = locations()
    prepare = plugin.prepare_resolved_invocation
    omitted = prepare("inner", {"source": value, "parameters": {}})
    explicit = prepare(
        "inner", {"source": value, "parameters": {"year": 2000, "count": 2}}
    )
    assert omitted.kind == "location_operation"
    assert omitted.identity == explicit.identity
    assert omitted.identity != replace(omitted, kind="metric").identity


@pytest.mark.parametrize("field", ["crs", "features"])
def test_preparation_rejects_unvalidated_nested_location_models(*, field: str) -> None:
    _, plugins = implementation()
    prepare = plugins["tables"].prepare_resolved_invocation
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


def test_identity_owns_callers_and_uses_cached_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    value = locations({"count": 1})
    equal = locations({"count": 1})
    parameters = Producer()
    inputs = {"source": value}
    cls = PreparedResolvedInvocation
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
