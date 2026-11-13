from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import geopandas as gpd
import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    RootModel,
    create_model,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)
from typing_extensions import TypeForm

from lyra.contracts.errors import MetricInputError, PluginDefinitionError
from lyra.contracts.schema import check_request_schema
from lyra.runtime.invocation import BoundPlugin
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.parameters import (
    MetricParameters,
    build_parameter_schema,
    parameter_values,
    validate_parameter_model,
)
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_resolution import Provider
from tests.fixtures.contract_plugin.metrics import CAPACITY_OUTPUT

if TYPE_CHECKING:
    from collections.abc import Iterator


def register(model: type[BaseModel]) -> PluginDefinition:
    def handler(parameters: BaseModel, location: gpd.GeoDataFrame) -> object:
        return parameters, location

    handler.__annotations__["parameters"] = model
    handler.__annotations__["location"] = gpd.GeoDataFrame
    decorated = metric(
        inputs={"location": LocationDependency(provider=Provider.id)},
        code_revision="1",
        name="test",
        description="Test",
        outputs={"test": CAPACITY_OUTPUT},
    )(handler)
    assert decorated is handler
    return PluginDefinition(metrics=[handler], resources={"locations": Provider})


@pytest.mark.parametrize(
    "annotation",
    [
        Any,
        tuple[int, ...],
        set[str],
        bytes,
        date,
        Decimal,
        dict[int, str],
        list,
        dict,
        Literal[1, "a"],
        Annotated[int, PlainSerializer(str)],
    ],
)
def test_unsupported_types(annotation: TypeForm[Any]) -> None:
    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        value=(annotation, Field(description="Value")),
    )
    with pytest.raises(PluginDefinitionError, match=r"parameters\.value"):
        register(model)


@pytest.mark.parametrize(
    "field",
    [
        Field(description="Value", alias="other"),
        Field(description="Value", json_schema_extra={"type": "string"}),
        Field(default=1, description="Value", validate_default=False),
        Field(description="Value", exclude=True),
        Field(description="Value", exclude_if=lambda value: value is None),
        Field(),
    ],
)
def test_invalid_field_contracts(field: object) -> None:
    model = create_model("Parameters", __base__=MetricParameters, value=(int, field))
    with pytest.raises(PluginDefinitionError):
        register(model)


class Permissive(BaseModel):
    value: int = Field(description="Value")


class Recursive(MetricParameters):
    children: list[Recursive] = Field(description="Children")


class Serializer(MetricParameters):
    value: int = Field(description="Value")

    @field_serializer("value")
    @staticmethod
    def serialize(value: int) -> str:
        return str(value)


@pytest.mark.parametrize("model", [Permissive, Recursive, Serializer, RootModel[int]])
def test_unsupported_models(model: type[BaseModel]) -> None:
    with pytest.raises(PluginDefinitionError):
        validate_parameter_model(model, "test")


def test_nested_model_configuration() -> None:
    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        nested=(Permissive, Field(description="Nested")),
    )
    with pytest.raises(PluginDefinitionError, match=r"parameters\.nested"):
        register(model)


class Values(MetricParameters):
    choice: Literal["only"] = Field(description="Choice")
    values: list[int | None] = Field(description="Values")
    lookup: dict[str, float] = Field(description="Lookup")


def test_json_types_and_local_definitions() -> None:
    plugin = register(Values)
    binding = BoundPlugin(plugin)
    good = {"choice": "only", "values": [1, None], "lookup": {"a": 1.5}}
    assert binding.prepare_parameters("test", good).model_dump() == good
    schema = plugin.describe("test").manifest.request_schema
    check_request_schema(schema)
    assert schema["properties"]["parameters"]["properties"]["choice"]["const"] == "only"
    for value in [{**good, "lookup": {1: 2}}, {**good, "lookup": {"a": float("nan")}}]:
        with pytest.raises(MetricInputError):
            binding.prepare_parameters("test", value)


class Semantic(MetricParameters):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"lower": 2, "upper": 1}]}
    )
    lower: int = Field(description="Lower", examples=[1])
    upper: int = Field(description="Upper", examples=[2])

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            message = "invalid interval"
            raise ValueError(message)
        return self


def test_examples_do_not_run_semantic_validation() -> None:
    binding = BoundPlugin(register(Semantic))
    with pytest.raises(MetricInputError, match="invalid interval"):
        binding.prepare_parameters("test", {"lower": 2, "upper": 1})


def test_rejects_handler_signatures() -> None:
    def missing(location: gpd.GeoDataFrame) -> object:
        return location

    def positional(location: gpd.GeoDataFrame, /) -> object:
        return location

    def variadic(*location: gpd.GeoDataFrame) -> object:
        return location

    def keywords(**location: gpd.GeoDataFrame) -> object:
        return location

    def default(location: gpd.GeoDataFrame | None = None) -> object:
        return location

    def unknown(other: str) -> object:
        return other

    missing.__annotations__.pop("location")
    for handler in [
        missing,
        positional,
        variadic,
        keywords,
        default,
        unknown,
    ]:
        with pytest.raises(PluginDefinitionError):
            metric(
                code_revision="1",
                name="test",
                description="Test",
                outputs={"test": CAPACITY_OUTPUT},
            )(handler)


def test_return_annotation_is_only_descriptive() -> None:
    def handler(*, location: gpd.GeoDataFrame) -> object:
        return location

    handler.__annotations__["return"] = "UnimportedDataFrame"
    metric(
        inputs={"location": LocationDependency(provider=Provider.id)},
        code_revision="1",
        name="test",
        description="Test",
        outputs={"test": CAPACITY_OUTPUT},
    )(handler)
    assert PluginDefinition(metrics=[handler]).metric_names == ("test",)


def test_registration_rejects_duplicates_and_undecorated() -> None:
    def handler(location: gpd.GeoDataFrame) -> object:
        return location

    with pytest.raises(PluginDefinitionError):
        PluginDefinition(metrics=[handler])
    decorated = metric(
        inputs={"location": LocationDependency(provider=Provider.id)},
        code_revision="1",
        name="test",
        description="Test",
        outputs={"test": CAPACITY_OUTPUT},
    )(handler)
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(metrics=[decorated, decorated])


@pytest.mark.parametrize(
    "reference", ["https://example.com/schema", "#/$defs/missing", "#/properties/value"]
)
def test_disallow_external_or_invalid_schema_refs(reference: str) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": reference,
    }
    with pytest.raises(ValueError, match="reference"):
        check_request_schema(schema)


def test_defaults_containing_ref_are_data() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "default": {"$ref": "ordinary data"},
    }
    check_request_schema(schema)


def test_pydantic_schema_generation_failure_has_metric_context() -> None:
    class Parameters(MetricParameters):
        mapping: dict[str, str] = Field(
            default={"$ref": "ordinary data"}, description="Mapping"
        )

    with pytest.raises(PluginDefinitionError, match=r"test.*request schema"):
        register(Parameters)


def test_rejects_async_and_generator() -> None:
    async def asynchronous(location: gpd.GeoDataFrame) -> object:
        await asyncio.sleep(0)
        return location

    def generator(location: gpd.GeoDataFrame) -> Iterator[gpd.GeoDataFrame]:
        yield location

    for handler in [asynchronous, generator]:
        with pytest.raises(PluginDefinitionError, match="synchronous"):
            metric(
                code_revision="1",
                name="test",
                description="Test",
                outputs={"test": CAPACITY_OUTPUT},
            )(handler)


def test_strict_models_can_reject_schema_valid_numbers() -> None:
    class Strict(MetricParameters):
        value: int = Field(strict=True, description="Integer")

    binding = BoundPlugin(register(Strict))
    assert binding.prepare_parameters("test", {"value": 1}).model_dump() == {"value": 1}
    with pytest.raises(MetricInputError):
        binding.prepare_parameters("test", {"value": 1.0})


class Filters(MetricParameters):
    tags: list[str] = Field(default_factory=list)


class Effective(MetricParameters):
    filters: Filters = Field(default_factory=Filters, description="Filters")
    options: JsonValue = Field(default_factory=dict, description="Options")
    label: str | None = Field(None, description="Label")
    count: int = Field(1, ge=0, description="Count")

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def normalize_tags(self) -> Self:
        self.filters.tags = sorted(set(self.filters.tags))
        return self


def test_factories_and_normalized_json_roundtrip() -> None:
    schema = build_parameter_schema("effective", Effective)
    assert schema is not None
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    assert "JsonValue" in definitions
    first = Effective()
    second = Effective()
    first.filters.tags.append("local")
    assert second.filters.tags == []
    expected = {"filters": {"tags": []}, "options": {}, "label": None, "count": 1}
    assert parameter_values(second) == expected
    supplied = {
        "filters": {"tags": ["z", "a", "a"]},
        "options": {"nested": [None, True, 1, 0.5, {"name": "north"}]},
        "label": " north ",
    }
    normalized = parameter_values(Effective.model_validate(supplied))
    assert normalized["filters"] == {"tags": ["a", "z"]}
    assert normalized["label"] == "north"
    restored = Effective.model_validate_json(json.dumps(normalized))
    assert parameter_values(restored) == normalized
    filters = normalized["filters"]
    assert isinstance(filters, dict)
    tags = filters["tags"]
    assert isinstance(tags, list)
    tags.append("changed")
    assert restored.filters.tags == ["a", "z"]


@pytest.mark.parametrize(
    "value",
    [object(), {1: "bad"}, {"nested": [float("nan")]}, {"nested": [float("inf")]}],
)
def test_effective_json_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(ValueError, match=r"validation error|Out of range"):
        parameter_values(Effective.model_validate({"options": value}))


def test_defaults_are_validated_by_pydantic() -> None:
    class InvalidDefault(MetricParameters):
        count: int = Field(default=-1, ge=0, description="Count")

    build_parameter_schema("invalid", InvalidDefault)
    with pytest.raises(ValueError, match="greater than or equal"):
        InvalidDefault()
    with pytest.raises(ValueError, match="Extra inputs"):
        Effective.model_validate({"unknown": 1})
    with pytest.raises(ValueError, match="Field required"):
        Values.model_validate({})


def test_discovery_does_not_execute_factories_or_validators() -> None:
    def factory() -> list[str]:
        pytest.fail("Discovery executed a default factory")

    class Parameters(MetricParameters):
        values: list[str] = Field(default_factory=factory, description="Values")

        @model_validator(mode="after")
        def validate_values(self) -> Self:
            pytest.fail(f"Discovery executed a validator: {self}")

    register(Parameters)


def test_nested_model_serializers_are_rejected() -> None:
    class Custom(MetricParameters):
        @model_serializer
        def serialize(self) -> dict[str, str]:
            return {"custom": type(self).__name__}

    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        value=(Custom, Field(description="Value")),
    )
    with pytest.raises(PluginDefinitionError, match="serializers"):
        register(model)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nested_float_fields_are_finite(number: float) -> None:
    class Nested(MetricParameters):
        numbers: list[float]

    class Parameters(MetricParameters):
        nested: Nested = Field(description="Nested numbers")

    with pytest.raises(ValueError, match="finite number"):
        Parameters.model_validate({"nested": {"numbers": [number]}})


def test_nested_exclusions_are_rejected() -> None:
    class Nested(MetricParameters):
        hidden: int = Field(1, exclude=True)

    model = create_model(
        "Parameters",
        __base__=MetricParameters,
        nested=(Nested, Field(default_factory=Nested, description="Nested")),
    )
    with pytest.raises(
        PluginDefinitionError, match=r"parameters.nested.hidden.*exclusions"
    ):
        register(model)


@pytest.mark.parametrize(
    "value",
    [object(), {1: "bad"}, {"nested": [float("nan")]}, {"nested": [float("inf")]}],
)
def test_serialization_rejects_invalid_effective_values(value: object) -> None:
    model = Effective().model_copy(update={"options": value})
    with pytest.raises(ValueError, match=r"serializ|validation error|Out of range"):
        parameter_values(model)
