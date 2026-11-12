"""Mixed bindings, ownership, and preparation through graph execution."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from lyra.contracts.errors import MetricInputError
from lyra.data import content_fingerprints
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.locations import Locations
from lyra.runtime.arguments import (
    PreparedResolvedInvocation,
)
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    producer_result,
)
from test_support.location_schema import column, contract, declaration, locations
from tests.graph_arguments_helpers import Producer
from tests.invocation_preparation_helpers import implementation


def test_nested_owner_forwarding_defaults_and_normalized_identity() -> None:
    seen: list[tuple[str, Locations, Producer]] = []
    catalog, plugins = implementation(seen)
    alias = locations({"count": 2})
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {
            "source": locations({"count": 1}),
            "alias": alias,
            "parameters": {"year": 2024},
        },
    )
    executor = GraphExecutor(catalog, plugins, Mock())
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
    catalog, plugins = implementation(seen, invalid)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations({"count": 1}), "alias": locations(), "parameters": {}},
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
    seen: list[tuple[str, Locations, Producer]] = []
    catalog, plugins = implementation(seen)
    owner = plugins["root"].prepare_resolved_invocation(
        "root",
        {"source": locations(), "alias": locations(), "parameters": {}},
    )
    # Unvalidated copies must fail before any operation is evaluated.
    owner = replace(owner, parameters=Producer().model_copy(update={"year": "bad"}))

    with pytest.raises(GraphExecutionError, match="year"):
        GraphExecutor(catalog, plugins, Mock()).execute(owner)
    assert seen == []


def test_identity_kind_receiving_names_feature_order_and_mapping_order() -> None:
    value = locations({"count": 1}, {"count": 2})
    first = PreparedResolvedInvocation("node", Producer(), {"source": value})
    assert (
        first.identity
        != replace(
            first, parameters=first.inspect_parameters(), kind="location_operation"
        ).identity
    )
    assert (
        first.identity
        != replace(
            first, parameters=first.inspect_parameters(), locations={"alias": value}
        ).identity
    )
    reversed_value = fixture_locations(
        value.to_dict() | {"features": list(reversed(value.to_features()))},
        schema=output_schema(count=int),
    )
    assert (
        first.identity
        != replace(
            first,
            parameters=first.inspect_parameters(),
            locations={"source": reversed_value},
        ).identity
    )
    assert (
        replace(
            first,
            parameters=first.inspect_parameters(),
            locations={"a": value, "b": value},
        ).identity
        == replace(
            first,
            parameters=first.inspect_parameters(),
            locations={"b": value, "a": value},
        ).identity
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
    effective = prepared.inspect_parameters()
    assert isinstance(effective, Producer)
    effective.year += 1
    assert prepared.identity == expected
    exported = prepared.arguments()["parameters"]
    assert exported == Producer()
    assert exported is not effective
