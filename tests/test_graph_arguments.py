from typing import TYPE_CHECKING, Any

import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    Input,
    Locations,
    MetricInputError,
    MetricParameters,
    PluginDefinition,
    metric,
)
from lyra.sdk.arguments import PreparedInvocation, prepare_dependency
from pydantic import Field, field_validator

from tests.location_helpers import scoped_payload
from tests.test_plugin_graph import graph, node

if TYPE_CHECKING:
    from collections.abc import Callable


class Consumer(MetricParameters):
    year: int | None = Field(default=2020, description="Year")
    baseline: int = Field(default=2010, description="Baseline year")
    unrelated: str = Field(default="ignored", description="Consumer only")


class Producer(MetricParameters):
    year: int = Field(default=2000, description="Year")
    count: int = Field(default=2, description="Producer default")


class Required(MetricParameters):
    year: int = Field(description="Year")


class Nullable(MetricParameters):
    year: int | None = Field(default=2000, description="Year")


class Objects(MetricParameters):
    settings: dict[str, int] = Field(default={"a": 1, "b": 2}, description="Settings")
    order: list[int] = Field(default=[1, 2], description="Ordered values")


class Normalized(MetricParameters):
    label: str = Field(description="Normalized label")

    @field_validator("label")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip().lower()


def plugin(
    model: type[MetricParameters] | None,
    *,
    name: str = "producer",
    spatial: bool = False,
) -> PluginDefinition:
    def parameterized(parameters: MetricParameters) -> object:
        pytest.fail(f"Argument preparation executed a handler: {parameters}")

    def independent() -> object:
        pytest.fail("Argument preparation executed a handler")

    def located(location: Locations, bounds: Locations) -> object:
        pytest.fail(f"Argument preparation executed a handler: {location}, {bounds}")

    handler: Callable[..., object] = located if spatial else independent
    if model is not None:
        handler = parameterized
        handler.__annotations__["parameters"] = model
    return PluginDefinition(
        metrics=[
            metric(name=name, description=name, outputs={name: pa.DataFrameSchema({})})(
                handler
            )
        ]
    )


def resolve(
    consumer: PreparedInvocation,
    model: type[MetricParameters] | None,
    overrides: dict[str, object] | None = None,
    *,
    output: str = "producer",
) -> PreparedInvocation:
    assert consumer.parameters is None or isinstance(
        consumer.parameters, MetricParameters
    )
    source_model = (
        type(consumer.parameters) if consumer.parameters is not None else None
    )
    registry = graph(
        node(
            "consumer",
            parameters=source_model,
            inputs={"table": Dependency(output, parameters=overrides or {})},
        ),
        node("producer", parameters=model, outputs=("producer", "second")),
    )
    return prepare_dependency(
        consumer, registry.dependencies["consumer"]["table"], plugin(model)
    )


def invocation(
    model: type[MetricParameters], values: dict[str, object]
) -> PreparedInvocation:
    return plugin(model, name="consumer").prepare_invocation(
        "consumer", {"parameters": values}
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
    consumer = PreparedInvocation("consumer", None, {})
    result = resolve(consumer, Producer)
    assert result.parameters == Producer()
    empty = resolve(invocation(Consumer, {}), None)
    assert empty.arguments() == {}
    assert plugin(None).prepare_invocation("producer", {}).identity == empty.identity


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
        node("consumer", parameters=Required, inputs={"table": Dependency("producer")}),
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
            inputs={"table": Dependency("middle", parameters={"year": 1990})},
        ),
        node(
            "middle",
            parameters=Producer,
            inputs={
                "table": Dependency(
                    "producer",
                    parameters={"year": Input("parameters.year")},
                )
            },
        ),
        node("producer", parameters=Required),
    )
    root = plugin(Consumer, name="root").prepare_invocation("root", {"parameters": {}})
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
        normalized.prepare_invocation(
            "producer", {"parameters": {"label": " A "}}
        ).identity
        == normalized.prepare_invocation(
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
        .prepare_invocation("other", {"parameters": {"year": 2020}})
        .identity
        != first.identity
    )


def geometry() -> dict[str, Any]:
    return {
        "collection": "test:units",
        "unit_type": "lot",
        "crs": {"type": "name", "properties": {"name": "EPSG:6372"}},
        "features": [
            {
                "id": "area",
                "type": "Feature",
                "properties": {"label": "native"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                },
            }
        ],
    }


def test_spatial_forwarding_preserves_native_objects() -> None:
    implementation = plugin(None, spatial=True)
    location = Locations.model_validate(scoped_payload(geometry()))
    bounds = Locations.model_validate(scoped_payload(geometry()))
    consumer = PreparedInvocation(
        "consumer", None, {"location": location, "bounds": bounds}
    )
    registry = graph(
        node(
            "consumer",
            spatial={"location", "bounds"},
            inputs={"table": Dependency("producer")},
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    result = prepare_dependency(
        consumer, registry.dependencies["consumer"]["table"], implementation
    )
    assert result.spatial["location"] is location
    assert result.spatial["bounds"] is bounds
    assert result.spatial["location"].model_dump() == location.model_dump()
    assert resolve(consumer, None).spatial == {}
    changed = geometry()
    changed["features"][0]["geometry"]["coordinates"][0].reverse()
    altered = implementation.prepare_invocation(
        "producer", {"location": changed, "bounds": bounds}
    )
    assert altered.identity != result.identity
    changed = geometry()
    changed["crs"]["properties"]["name"] = "EPSG:4326"
    assert (
        implementation.prepare_invocation(
            "producer", {"location": changed, "bounds": bounds}
        ).identity
        != result.identity
    )


class TextYear(MetricParameters):
    year: str = Field(default="invalid", description="Year label")


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
            inputs={"table": Dependency("producer")},
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    spatial = {
        "location": Locations.model_validate(scoped_payload(geometry())),
        "bounds": Locations.model_validate(scoped_payload(geometry())),
    }
    del spatial[missing]
    edge = registry.dependencies["consumer"]["table"]
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(
            PreparedInvocation("consumer", Consumer(), spatial),
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
                )
            },
        ),
        node("producer", parameters=Producer),
    )
    edge = registry.dependencies["consumer"]["table"]
    with pytest.raises(MetricInputError, match="missing referenced input") as error:
        prepare_dependency(
            PreparedInvocation("consumer", None, {}),
            edge,
            plugin(Producer),
        )
    assert error.value.path == "parameters.baseline"
    assert error.value.metric == "producer"
    assert edge.diagnostic in error.value.message


class ExcludedYear(MetricParameters):
    year: int = Field(default=2020, description="Year", exclude=True)


class NestedExcluded(MetricParameters):
    settings: ExcludedYear = Field(description="Settings")
    history: list[ExcludedYear] = Field(description="History")
    named: dict[str, ExcludedYear] = Field(description="Named settings")


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
