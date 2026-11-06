import pytest

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.runtime.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.sdk.dependencies import Dependency
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    scoped_payload,
)
from test_support.plugin_graph import graph, node
from tests.graph_arguments_helpers import (
    Consumer,
    ExcludedYear,
    NestedExcluded,
    Normalized,
    Nullable,
    Objects,
    Producer,
    Required,
    TextYear,
    geometry,
    invocation,
    plugin,
    resolve,
)


@pytest.mark.parametrize(
    ("values", "overrides", "expected"),
    [
        ({}, {}, 2020),
        ({"year": 2024}, {}, 2024),
        ({}, {"year": 1990}, 1990),
        ({}, {"year": Input("parameters.baseline")}, 2010),
    ],
)
def test_precedence(
    values: dict[str, object], overrides: dict[str, object], expected: int
) -> None:
    result = resolve(invocation(Consumer, values), Producer, overrides)
    assert result.parameters is not None
    assert result.parameters.model_dump() == {"year": expected, "count": 2}


def test_producer_defaults_and_parameterless_nodes() -> None:
    consumer = PreparedResolvedInvocation("consumer", None, {})
    result = resolve(consumer, Producer)
    assert result.parameters == Producer()
    empty = resolve(invocation(Consumer, {}), None)
    assert empty.arguments() == {}
    assert (
        plugin(None).prepare_resolved_invocation("producer", {}).identity
        == empty.identity
    )


@pytest.mark.parametrize("overrides", [{}, {"year": None}])
def test_explicit_null_does_not_default(overrides: dict[str, object]) -> None:
    consumer = invocation(Consumer, {"year": None})
    assert resolve(consumer, Nullable, overrides).parameters == Nullable(year=None)
    with pytest.raises(MetricInputError, match=r"parameters\.year") as error:
        resolve(consumer, Producer, overrides)
    assert "input 'table'" in str(error.value)
    assert "producer 'producer'" in str(error.value)
    assert isinstance(error.value.__cause__, MetricInputError)


def test_invalid_forwarded_value_and_missing_required() -> None:
    consumer = invocation(Objects, {})
    # Supply paths are checked structurally; runtime checks still protect absent values.
    registry = graph(
        node(
            "consumer",
            parameters=Required,
            inputs={"table": Dependency("producer", producer="producer")},
        ),
        node("producer", parameters=Required),
    )
    with pytest.raises(MetricInputError, match="year"):
        prepare_dependency(
            consumer, registry.dependencies["consumer"]["table"], plugin(Required)
        )
    with pytest.raises(MetricInputError, match=r"parameters\.year"):
        resolve(invocation(Consumer, {}), Producer, {"year": "invalid"})


def test_chain_references_current_effective_parameters() -> None:
    registry = graph(
        node(
            "root",
            parameters=Consumer,
            inputs={
                "table": Dependency(
                    "middle", parameters={"year": 1990}, producer="middle"
                )
            },
        ),
        node(
            "middle",
            parameters=Producer,
            inputs={
                "table": Dependency(
                    "producer",
                    parameters={"year": Input("parameters.year")},
                    producer="producer",
                )
            },
        ),
        node("producer", parameters=Required),
    )
    root = plugin(Consumer, name="root").prepare_resolved_invocation(
        "root", {"parameters": {}}
    )
    middle = prepare_dependency(
        root, registry.dependencies["root"]["table"], plugin(Producer, name="middle")
    )
    result = prepare_dependency(
        middle, registry.dependencies["middle"]["table"], plugin(Required)
    )
    assert result.parameters == Required(year=1990)


def test_whole_objects_replace_defaults_and_forwarded_values() -> None:
    consumer = invocation(Objects, {"settings": {"c": 3}})
    assert resolve(consumer, Objects).parameters == Objects(settings={"c": 3})
    assert resolve(consumer, Objects, {"settings": {"d": 4}}).parameters == Objects(
        settings={"d": 4}
    )
    assert resolve(
        consumer, Objects, {"settings": Input("parameters.settings")}
    ).parameters == Objects(settings={"c": 3})


def test_identity_uses_effective_producer_inputs() -> None:
    first = resolve(invocation(Consumer, {}), Producer)
    second = resolve(
        invocation(Consumer, {"unrelated": "different"}), Producer, output="second"
    )
    assert first.identity == second.identity
    assert (
        first.identity
        != resolve(invocation(Consumer, {"year": 2001}), Producer).identity
    )
    normalized = plugin(Normalized)
    assert (
        normalized.prepare_resolved_invocation(
            "producer", {"parameters": {"label": " A "}}
        ).identity
        == normalized.prepare_resolved_invocation(
            "producer", {"parameters": {"label": "a"}}
        ).identity
    )
    assert (
        resolve(invocation(Objects, {"settings": {"a": 1, "b": 2}}), Objects).identity
        == resolve(
            invocation(Objects, {"settings": {"b": 2, "a": 1}}), Objects
        ).identity
    )
    assert (
        resolve(invocation(Objects, {}), Objects).identity
        != resolve(invocation(Objects, {"order": [2, 1]}), Objects).identity
    )
    assert (
        plugin(Producer, name="other")
        .prepare_resolved_invocation("other", {"parameters": {"year": 2020}})
        .identity
        != first.identity
    )


def test_spatial_forwarding_preserves_native_objects() -> None:
    implementation = plugin(None, spatial=True)
    location = fixture_locations(
        scoped_payload(geometry()), schema=output_schema(label=str)
    )
    bounds = fixture_locations(
        scoped_payload(geometry()), schema=output_schema(label=str)
    )
    consumer = PreparedResolvedInvocation(
        "consumer", None, {"location": location, "bounds": bounds}
    )
    registry = graph(
        node(
            "consumer",
            spatial={"location", "bounds"},
            inputs={"table": Dependency("producer", producer="producer")},
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    result = prepare_dependency(
        consumer, registry.dependencies["consumer"]["table"], implementation
    )
    assert result.locations["location"] == location
    assert result.locations["bounds"] == bounds
    assert result.locations["location"].to_dict() == location.to_dict()
    assert resolve(consumer, None).locations == {}
    changed = geometry()
    changed["features"][0]["geometry"]["coordinates"][0].reverse()
    altered = implementation.prepare_resolved_invocation(
        "producer",
        {
            "location": fixture_locations(changed, schema=output_schema(label=str)),
            "bounds": bounds,
        },
    )
    assert altered.identity != result.identity
    changed = geometry()
    changed["crs"]["properties"]["name"] = "EPSG:4326"
    assert (
        implementation.prepare_resolved_invocation(
            "producer",
            {
                "location": fixture_locations(changed, schema=output_schema(label=str)),
                "bounds": bounds,
            },
        ).identity
        != result.identity
    )


def test_invalid_forwarded_value_uses_producer_validation() -> None:
    with pytest.raises(MetricInputError, match=r"parameters\.year"):
        resolve(invocation(TextYear, {}), Producer)


@pytest.mark.parametrize("missing", ["location", "bounds"])
def test_missing_spatial_inputs_keep_edge_context(missing: str) -> None:
    registry = graph(
        node(
            "consumer",
            parameters=Consumer,
            spatial={"location", "bounds"},
            inputs={"table": Dependency("producer", producer="producer")},
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    spatial = {
        "location": fixture_locations(
            scoped_payload(geometry()), schema=output_schema(label=str)
        ),
        "bounds": fixture_locations(
            scoped_payload(geometry()), schema=output_schema(label=str)
        ),
    }
    del spatial[missing]
    edge = registry.dependencies["consumer"]["table"]
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(
            PreparedResolvedInvocation("consumer", Consumer(), spatial),
            edge,
            plugin(None, spatial=True),
        )
    assert error.value.path == missing
    assert error.value.metric == "producer"
    assert edge.diagnostic in error.value.message


def test_missing_referenced_inputs_keep_edge_context() -> None:
    registry = graph(
        node(
            "consumer",
            parameters=Consumer,
            inputs={
                "table": Dependency(
                    "producer",
                    parameters={"year": Input("parameters.baseline")},
                    producer="producer",
                )
            },
        ),
        node("producer", parameters=Producer),
    )
    edge = registry.dependencies["consumer"]["table"]
    with pytest.raises(MetricInputError, match="missing referenced input") as error:
        prepare_dependency(
            PreparedResolvedInvocation("consumer", None, {}),
            edge,
            plugin(Producer),
        )
    assert error.value.path == "parameters.baseline"
    assert error.value.metric == "producer"
    assert edge.diagnostic in error.value.message


def test_serialization_exclusions_do_not_hide_effective_fields() -> None:
    consumer = invocation(ExcludedYear, {"year": 2024})
    assert resolve(consumer, Required).parameters == Required(year=2024)
    assert resolve(
        consumer, Required, {"year": Input("parameters.year")}
    ).parameters == Required(year=2024)
    assert consumer.identity != invocation(ExcludedYear, {"year": 2025}).identity


def test_nested_model_fields_are_preserved_for_forwarding_and_identity() -> None:
    values: dict[str, object] = {
        "settings": {"year": 2024},
        "history": [{"year": 2023}],
        "named": {"baseline": {"year": 2022}},
    }
    consumer = invocation(NestedExcluded, values)
    result = resolve(consumer, NestedExcluded)
    assert result.parameters == consumer.parameters
    for field, changed in [
        ("settings", {"year": 2000}),
        ("history", [{"year": 2000}]),
        ("named", {"baseline": {"year": 2000}}),
    ]:
        assert (
            consumer.identity
            != invocation(NestedExcluded, {**values, field: changed}).identity
        )
