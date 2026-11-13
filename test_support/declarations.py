"""Build bound internal node fixtures for execution and storage unit tests."""

from collections.abc import Callable, Mapping, Sequence
from types import FunctionType, MappingProxyType
from typing import TypedDict, Unpack, get_type_hints

from pydantic import BaseModel, JsonValue

from lyra.contracts.dependencies import Dependency
from lyra.contracts.locations import LocationContract, LocationContractReference
from lyra.contracts.origins import Origin
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)
from lyra.sdk.context import RunContext
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.parameters import build_parameter_schema
from lyra.sdk.plugin import (
    NodeDefinition,
    NodeImplementation,
)
from lyra.sdk.plugin import (
    PluginDefinition as SDKPluginDefinition,
)
from lyra.sdk.resources import resource_annotation
from lyra.sdk.storage_schema import describe


class _Options(TypedDict, total=False):
    inputs: Mapping[str, Dependency]
    private: bool
    origins: Sequence[Origin]
    source_bindings: Mapping[str, tuple[LocationContractReference, ...]]
    canonical: bool


class _OperationOptions(_Options, total=False):
    locations: Mapping[str, LocationContract]


def _hints(function: Callable) -> dict:
    assert isinstance(function, FunctionType)
    annotations = {
        name: annotation
        for name, annotation in function.__annotations__.items()
        if name != "return"
    }
    holder = type("FixtureAnnotations", (), {"__annotations__": annotations})
    return get_type_hints(holder, globalns=function.__globals__, include_extras=True)


def metric(
    *,
    name: str,
    code_revision: str,
    description: str,
    outputs: Mapping[str, OutputSchema],
    locations: Mapping[str, LocationContract] | None = None,
    **options: Unpack[_Options],
) -> Callable:
    return _node((name, code_revision, description), outputs, locations or {}, options)


def location_operation(
    *,
    name: str,
    code_revision: str,
    description: str,
    output: OutputSchema | LocationOutputSchema,
    output_contract: LocationContract,
    **options: Unpack[_OperationOptions],
) -> Callable:
    schema = (
        output
        if isinstance(output, LocationOutputSchema)
        else LocationOutputSchema(output)
    )
    return _node(
        (name, code_revision, description),
        {},
        options.get("locations", {}),
        options,
        operation=(schema, output_contract),
    )


class _Common(TypedDict):
    name: str
    code_revision: str
    description: str
    parameters_schema: dict[str, JsonValue] | None
    location_inputs: dict[str, LocationInputDeclaration]
    inputs: dict[str, Dependency]
    resources: dict[str, str]


def _node(
    identity: tuple[str, str, str],
    outputs: Mapping[str, OutputSchema],
    locations: Mapping[str, LocationContract],
    options: _Options,
    *,
    operation: tuple[LocationOutputSchema, LocationContract] | None = None,
) -> Callable:
    name, revision, description = identity

    def decorate(function: Callable) -> Callable:
        inputs = options.get("inputs", {})
        hints = _hints(function)
        candidate = hints.get("parameters")
        parameters = (
            candidate
            if isinstance(candidate, type) and issubclass(candidate, BaseModel)
            else None
        )
        context = hints.get("context") is RunContext
        common: _Common = {
            "name": name,
            "code_revision": revision,
            "description": description,
            "parameters_schema": build_parameter_schema(name, parameters),
            "location_inputs": {
                key: LocationInputDeclaration(
                    contract=value,
                    source_bindings=options.get("source_bindings", {}).get(key, ()),
                )
                for key, value in locations.items()
            },
            "inputs": dict(inputs),
            "resources": {
                key: requirement[1].name
                for key, annotation in hints.items()
                if (requirement := resource_annotation(annotation)) is not None
            },
        }
        compiled = {key: compile_output(value) for key, value in outputs.items()}
        manifest = (
            LocationOperationManifest(
                **common,
                output_contract=operation[1],
                canonical=options.get("canonical", False),
            )
            if operation is not None
            else MetricManifest(
                **common,
                outputs={key: value[1] for key, value in compiled.items()},
                private=options.get("private", False),
                cache="full",
                origins=options.get("origins", ()),
            )
        )
        implementation = NodeImplementation(
            function,
            parameters,
            context,
            manifest,
            {key: value[0] for key, value in compiled.items()},
            operation[0] if operation else None,
            storage={key: describe(value[0]) for key, value in compiled.items()},
        )
        function.__dict__["__fixture_implementation__"] = implementation
        return function

    return decorate


class PluginDefinition(SDKPluginDefinition):
    def __init__(
        self,
        *,
        metrics: Sequence[Callable] = (),
        location_operations: Sequence[Callable] = (),
        resources: Mapping | None = None,
        route_preferences: Sequence[Sequence[Callable]] = (),
    ) -> None:
        native_metrics = [
            fn for fn in metrics if "__fixture_implementation__" not in fn.__dict__
        ]
        native_operations = [
            fn
            for fn in location_operations
            if "__fixture_implementation__" not in fn.__dict__
        ]
        nodes = {}
        if native_metrics or native_operations:
            native = SDKPluginDefinition(
                metrics=native_metrics,
                location_operations=native_operations,
                resources=resources,
            )
            nodes.update(
                {name: native.implementation(name) for name in native.node_names}
            )
        nodes.update(
            {
                value.manifest.name: value
                for function in (*metrics, *location_operations)
                if (value := function.__dict__.get("__fixture_implementation__"))
                is not None
            }
        )
        self._nodes = MappingProxyType(nodes)
        self._declarations = MappingProxyType(
            {
                name: NodeDefinition(
                    name=name,
                    kind=value.manifest.kind,
                    function=value.function,
                    parameters=value.parameters,
                    accepts_context=value.accepts_context,
                    code_revision=value.manifest.code_revision,
                    description=value.manifest.description,
                    inputs=value.manifest.inputs,
                    schemas=value.schemas,
                    storage={
                        name: describe(schema) for name, schema in value.schemas.items()
                    },
                    outputs=value.manifest.outputs
                    if value.manifest.kind == "metric"
                    else {},
                    resources=value.manifest.resources,
                )
                for name, value in nodes.items()
            }
        )
        self.resources = MappingProxyType(dict(resources or {}))
        self.route_preferences = tuple(
            tuple(
                function.__dict__["__fixture_implementation__"].manifest.name
                for function in route
            )
            for route in route_preferences
        )
