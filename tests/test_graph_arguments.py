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
from test_support.runtime_inputs import assert_records
from tests.graph_arguments_helpers import (
    Consumer,
    NestedSettings,
    Normalized,
    Nullable,
    Objects,
    Producer,
    Required,
    TextYear,
    YearSettings,
    geometry,
    invocation,
    location_edge,
    parameter_edge,
    plugin,
    resolve,
    scoped_inputs,
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
    effective = result.inspect_parameters()
    assert effective is not None
    assert effective.model_dump() == {"year": expected, "count": 2}


def test_producer_defaults_and_parameterless_nodes() -> None:
    consumer = PreparedResolvedInvocation("consumer", None, {})
    result = resolve(consumer, Producer)
    assert result.inspect_parameters() == Producer()
    empty = resolve(invocation(Consumer, {}), None)
    assert empty.arguments() == {}
    assert (
        plugin(None).prepare_resolved_invocation("producer", {}).identity
        == empty.identity
    )


@pytest.mark.parametrize("overrides", [{}, {"year": None}])
def test_explicit_null_does_not_default(overrides: dict[str, object]) -> None:
    consumer = invocation(Consumer, {"year": None})
    assert resolve(consumer, Nullable, overrides).inspect_parameters() == Nullable(
        year=None
    )
    with pytest.raises(MetricInputError, match=r"parameters\.year") as error:
        resolve(consumer, Producer, overrides)
    assert "input 'table'" in str(error.value)
    assert "producer 'producer'" in str(error.value)
    assert isinstance(error.value.__cause__, MetricInputError)


def test_invalid_forwarded_value_and_missing_required() -> None:
    consumer = invocation(Objects, {})
    # Supply paths are checked structurally; runtime checks still protect absent values.
    edge = parameter_edge(
        "consumer", Required, Dependency("producer", producer="producer"), Required
    )
    with pytest.raises(MetricInputError, match="year"):
        prepare_dependency(consumer, edge, plugin(Required))
    with pytest.raises(MetricInputError, match=r"parameters\.year"):
        resolve(invocation(Consumer, {}), Producer, {"year": "invalid"})


def test_chain_references_current_effective_parameters() -> None:
    middle_edge = parameter_edge(
        "root",
        Consumer,
        Dependency("middle", producer="middle", parameters={"year": 1990}),
        Producer,
    )
    source_edge = parameter_edge(
        "middle",
        Producer,
        Dependency(
            "producer",
            producer="producer",
            parameters={"year": Input("parameters.year")},
        ),
        Required,
    )
    root = plugin(Consumer, name="root").prepare_resolved_invocation(
        "root", {"parameters": {}}
    )
    middle = prepare_dependency(root, middle_edge, plugin(Producer, name="middle"))
    result = prepare_dependency(middle, source_edge, plugin(Required))
    assert result.inspect_parameters() == Required(year=1990)


def test_whole_objects_replace_defaults_and_forwarded_values() -> None:
    consumer = invocation(Objects, {"settings": {"c": 3}})
    assert resolve(consumer, Objects).inspect_parameters() == Objects(settings={"c": 3})
    assert resolve(
        consumer, Objects, {"settings": {"d": 4}}
    ).inspect_parameters() == Objects(settings={"d": 4})
    assert resolve(
        consumer, Objects, {"settings": Input("parameters.settings")}
    ).inspect_parameters() == Objects(settings={"c": 3})


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
        "consumer", None, scoped_inputs({"location": location, "bounds": bounds})
    )
    edge = location_edge()
    result = prepare_dependency(consumer, edge, implementation)
    assert_records(result.locations["location"], consumer.locations["location"])
    assert_records(result.locations["bounds"], consumer.locations["bounds"])
    assert resolve(consumer, None).locations == {}
    changed = geometry()
    changed["features"][0]["geometry"]["coordinates"][0].reverse()
    altered = implementation.prepare_resolved_invocation(
        "producer",
        scoped_inputs(
            {
                "location": fixture_locations(changed, schema=output_schema(label=str)),
                "bounds": bounds,
            }
        ),
    )
    assert altered.identity == result.identity
    changed = geometry()
    changed["crs"]["properties"]["name"] = "EPSG:4326"
    assert (
        implementation.prepare_resolved_invocation(
            "producer",
            scoped_inputs(
                {
                    "location": fixture_locations(
                        changed, schema=output_schema(label=str)
                    ),
                    "bounds": bounds,
                }
            ),
        ).identity
        == result.identity
    )


def test_invalid_forwarded_value_uses_producer_validation() -> None:
    with pytest.raises(MetricInputError, match=r"parameters\.year"):
        resolve(invocation(TextYear, {}), Producer)


@pytest.mark.parametrize("missing", ["location", "bounds"])
def test_missing_spatial_inputs_keep_edge_context(missing: str) -> None:
    spatial = {
        "location": fixture_locations(
            scoped_payload(geometry()), schema=output_schema(label=str)
        ),
        "bounds": fixture_locations(
            scoped_payload(geometry()), schema=output_schema(label=str)
        ),
    }
    del spatial[missing]
    edge = location_edge()
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(
            PreparedResolvedInvocation("consumer", Consumer(), scoped_inputs(spatial)),
            edge,
            plugin(None, spatial=True),
        )
    assert error.value.path == missing
    assert error.value.metric == "producer"
    assert edge.diagnostic in error.value.message


def test_missing_referenced_inputs_keep_edge_context() -> None:
    edge = parameter_edge(
        "consumer",
        Consumer,
        Dependency(
            "producer",
            producer="producer",
            parameters={"year": Input("parameters.baseline")},
        ),
        Producer,
    )
    with pytest.raises(MetricInputError, match="missing referenced input") as error:
        prepare_dependency(
            PreparedResolvedInvocation("consumer", None, {}),
            edge,
            plugin(Producer),
        )
    assert error.value.path == "parameters.baseline"
    assert error.value.metric == "producer"
    assert edge.diagnostic in error.value.message


def test_effective_fields_are_forwarded_and_identify_invocations() -> None:
    consumer = invocation(YearSettings, {"year": 2024})
    assert resolve(consumer, Required).inspect_parameters() == Required(year=2024)
    assert resolve(
        consumer, Required, {"year": Input("parameters.year")}
    ).inspect_parameters() == Required(year=2024)
    assert consumer.identity != invocation(YearSettings, {"year": 2025}).identity


def test_nested_model_fields_are_preserved_for_forwarding_and_identity() -> None:
    values: dict[str, object] = {
        "settings": {"year": 2024},
        "history": [{"year": 2023}],
        "named": {"baseline": {"year": 2022}},
    }
    consumer = invocation(NestedSettings, values)
    result = resolve(consumer, NestedSettings)
    assert result.inspect_parameters() == consumer.inspect_parameters()
    for field, changed in [
        ("settings", {"year": 2000}),
        ("history", [{"year": 2000}]),
        ("named", {"baseline": {"year": 2000}}),
    ]:
        assert (
            consumer.identity
            != invocation(NestedSettings, {**values, field: changed}).identity
        )
