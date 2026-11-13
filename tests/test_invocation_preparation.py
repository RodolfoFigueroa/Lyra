"""Mixed bindings, ownership, and preparation through graph execution."""

from dataclasses import replace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from lyra.contracts.errors import MetricInputError
from lyra.runtime.arguments import (
    PreparedResolvedInvocation,
)
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from test_support.location_helpers import (
    producer_result,
)
from test_support.location_schema import column, contract, declaration, locations
from tests.graph_arguments_helpers import Producer
from tests.invocation_preparation_helpers import implementation, invocation_inputs


def test_nested_owner_forwarding_defaults_and_normalized_identity() -> None:
    seen: list[tuple[str, gpd.GeoDataFrame, Producer]] = []
    catalog, plugins = implementation(seen)
    alias = locations({"count": 2})
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        invocation_inputs(
            {
                "source": locations({"count": 1}),
                "alias": alias,
                "parameters": {"year": 2024},
            }
        ),
    )
    executor = GraphExecutor(catalog, plugins, Mock())
    executor.execute(owner)
    assert [name for name, _, _ in seen] == [
        "inner",
        "outer",
        "leaf",
        "middle",
        "root",
    ]
    pd.testing.assert_frame_equal(
        seen[0][1], invocation_inputs({"alias": alias})["alias"].frame
    )
    assert seen[0][2] == Producer(year=2024)
    expected = gpd.GeoDataFrame(
        {"location_id": ["derived"], "count": [3]}, geometry=[Point(1, 2)], crs=4326
    )
    for index, year in [(1, 1980), (2, 1990), (3, 1990)]:
        pd.testing.assert_frame_equal(seen[index][1][expected.columns], expected)
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
    seen: list[tuple[str, gpd.GeoDataFrame, Producer]] = []
    catalog, plugins = implementation(seen, invalid)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        invocation_inputs(
            {
                "source": locations({"count": 1}),
                "alias": locations({"count": 2}),
                "parameters": {},
            }
        ),
    )
    with pytest.raises(GraphExecutionError, match="inner") as error:
        GraphExecutor(catalog, plugins, Mock()).execute(owner)
    assert error.value.node == "inner"
    assert error.value.path == ("root", "middle", "outer", "inner")
    assert (
        error.value.receiving_input == "inputs.first.locations.source.locations.source"
    )
    assert [name for name, _, _ in seen] == ["inner"]


def test_invalid_operation_parameters_fail_before_evaluation() -> None:
    seen: list[tuple[str, gpd.GeoDataFrame, Producer]] = []
    catalog, plugins = implementation(seen)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        invocation_inputs(
            {"source": locations(), "alias": locations({"count": 2}), "parameters": {}}
        ),
    )
    # Unvalidated copies must fail before any operation is evaluated.
    owner = replace(owner, parameters=Producer().model_copy(update={"year": "bad"}))

    with pytest.raises(GraphExecutionError, match="year"):
        GraphExecutor(catalog, plugins, Mock()).execute(owner)
    assert seen == []


def test_identity_kind_receiving_names_feature_order_and_mapping_order() -> None:
    value = locations({"count": 1}, {"count": 2})
    first = PreparedResolvedInvocation(
        "node", Producer(), invocation_inputs({"source": value})
    )
    assert (
        first.identity
        != replace(
            first, parameters=first.inspect_parameters(), kind="location_operation"
        ).identity
    )
    assert (
        first.identity
        != replace(
            first,
            parameters=first.inspect_parameters(),
            locations=invocation_inputs({"alias": value}),
        ).identity
    )
    reversed_value = value.iloc[::-1].copy()
    assert (
        first.identity
        == replace(
            first,
            parameters=first.inspect_parameters(),
            locations=invocation_inputs({"source": reversed_value}),
        ).identity
    )
    assert (
        replace(
            first,
            parameters=first.inspect_parameters(),
            locations=invocation_inputs({"a": value, "b": value}),
        ).identity
        == replace(
            first,
            parameters=first.inspect_parameters(),
            locations=invocation_inputs({"b": value, "a": value}),
        ).identity
    )


def test_preparation_revalidates_malformed_location_instances() -> None:
    _, plugins = implementation()
    prepare = plugins["tables"].prepare_resolved_invocation
    malformed = object()
    with pytest.raises(MetricInputError, match="source"):
        prepare("leaf", invocation_inputs({"source": malformed, "parameters": {}}))


def test_plugin_preparation_includes_operation_kind_and_effective_defaults() -> None:
    _, plugins = implementation()
    plugin = plugins["operations"]
    value = locations()
    prepare = plugin.prepare_resolved_invocation
    omitted = prepare("inner", invocation_inputs({"source": value, "parameters": {}}))
    explicit = prepare(
        "inner",
        invocation_inputs({"source": value, "parameters": {"year": 2000, "count": 2}}),
    )
    assert omitted.kind == "location_operation"
    assert omitted.identity == explicit.identity
    assert (
        omitted.identity
        != replace(
            omitted, parameters=omitted.inspect_parameters(), kind="metric"
        ).identity
    )


@pytest.mark.parametrize("field", ["crs", "features"])
def test_preparation_rejects_unvalidated_nested_location_models(*, field: str) -> None:
    _, plugins = implementation()
    prepare = plugins["tables"].prepare_resolved_invocation
    value = locations({"count": 1})
    malformed = {field: {}}
    with pytest.raises(MetricInputError, match="source"):
        prepare("leaf", invocation_inputs({"source": malformed, "parameters": {}}))

    # Explicit imports produce usable runtime values.
    prepared = prepare(
        "leaf",
        invocation_inputs(
            {
                "source": value,
                "parameters": {},
            }
        ),
    )
    source = prepared.arguments()["source"]
    assert isinstance(source, gpd.GeoDataFrame)
    assert str(source.crs) == str(value.crs)
    assert source.iloc[:, 0].tolist() == value.iloc[:, 0].tolist()


def test_identity_owns_caller_records_parameters_and_working_frames() -> None:

    value = locations({"count": 1})
    equal = locations({"count": 1})
    parameters = Producer()
    inputs = invocation_inputs({"source": value})
    cls = PreparedResolvedInvocation
    prepared = cls("node", parameters, invocation_inputs(inputs))
    expected = prepared.identity
    assert (
        cls("node", Producer(), invocation_inputs({"source": equal})).identity
        == expected
    )
    parameters.year += 1
    inputs["source"].frame.iloc[0, 0] = "caller mutation"
    inputs.clear()
    assert prepared.identity == expected

    working = prepared.arguments()["source"]
    assert isinstance(working, gpd.GeoDataFrame)
    working.iloc[0, 0] = "handler mutation"
    working.attrs["provider_scope"] = {"provider_id": "forged"}
    assert prepared.identity == expected
    assert prepared.locations["source"].ids == tuple(value.iloc[:, 0])
    effective = prepared.inspect_parameters()
    assert isinstance(effective, Producer)
    effective.year += 1
    assert prepared.identity == expected
    exported = prepared.arguments()["parameters"]
    assert exported == Producer()
    assert exported is not effective
