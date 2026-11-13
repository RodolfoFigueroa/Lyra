"""Plugin declarations, registration and handler descriptors."""

import inspect
from collections.abc import Callable, Mapping, Sequence
from copy import copy, deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Self,
    TypedDict,
    TypeVar,
    Unpack,
)

from pydantic import BaseModel, TypeAdapter

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.origins import Origin
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    NodeManifest,
    PluginInfo,
    PluginManifest,
    validate_metric_name,
)
from lyra.contracts.providers import ProviderDeclaration
from lyra.contracts.revisions import ImplementationRevision
from lyra.contracts.strict import StrictBaseModel
from lyra.runtime.planning import native_bindings
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.handler import (
    _definition_message,
    _handler_arguments,
    _handler_hints,
    _location_description,
)
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.parameters import build_parameter_schema
from lyra.sdk.resources import (
    BaseIO,
    resource_annotation,
    resource_metadata,
    validate_resource_providers,
)

if TYPE_CHECKING:
    import pandera.pandas as pa

    from lyra.runtime.planning.node_declarations import CompiledNode
    from lyra.runtime.planning.provider_registry import RegisteredProvider

_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])
_DEFINITION_ATTRIBUTE = "__lyra_node_definition__"


class NodeDescription(StrictBaseModel):
    """Detached contract and runtime handler inspection metadata."""

    manifest: NodeManifest
    handler: str
    signature: str


@dataclass(frozen=True)
class NodeImplementation:
    """Registered handler, captured executable schemas and invocation requirements."""

    function: Callable[..., object]
    parameters: type[BaseModel] | None
    accepts_context: bool
    manifest: NodeManifest
    schemas: dict[str, "pa.DataFrameSchema"]
    location_schema: LocationOutputSchema | None = None
    native_inputs: Mapping[str, Dependency | LocationDependency] | None = None
    target: str | None = None


def _owned_location_schema(
    schema: LocationOutputSchema | None,
) -> LocationOutputSchema | None:
    if schema is None:
        return None
    view = copy(schema)
    view.metadata = schema.metadata.model_copy(deep=True)
    return view


@dataclass(frozen=True)
class NodeDefinition:
    """Unbound native declaration captured by a decorator."""

    name: str
    kind: Literal["metric", "location_operation"]
    function: Callable[..., object]
    parameters: type[BaseModel] | None
    accepts_context: bool
    code_revision: str
    description: str
    inputs: Mapping[str, Dependency | LocationDependency]
    schemas: Mapping[str, "pa.DataFrameSchema"]
    outputs: Mapping[str, OutputMetadata]
    resources: Mapping[str, str]
    target: str | None = None
    origins: frozenset[Origin] = frozenset()
    private: bool = False
    canonical: bool = False


class _MetricOptions(TypedDict, total=False):
    inputs: Mapping[str, Dependency | LocationDependency]
    origins: Sequence[Origin]
    private: bool


class _OperationOptions(TypedDict, total=False):
    canonical: bool


def metric(
    *,
    name: str,
    code_revision: str,
    description: str,
    outputs: Mapping[str, OutputSchema],
    **options: Unpack[_MetricOptions],
) -> Callable[[_HandlerT], _HandlerT]:
    """Declare native provider inputs and metric-table dependencies.

    Returns:
        A decorator preserving the directly callable synchronous handler.

    Raises:
        PluginDefinitionError: If unsupported options are supplied.
    """
    if options.keys() - {"inputs", "origins", "private"}:
        message = "Unsupported metric options"
        raise PluginDefinitionError(message)
    return _declare(
        name=name,
        code_revision=code_revision,
        description=description,
        outputs=outputs,
        inputs=options.get("inputs", {}),
        origins=options.get("origins", ()),
        private=options.get("private", False),
    )


def location_operation(
    *,
    name: str,
    code_revision: str,
    description: str,
    inputs: Mapping[str, LocationDependency],
    target: str,
    **options: Unpack[_OperationOptions],
) -> Callable[[_HandlerT], _HandlerT]:
    """Declare a transformation from one provider source to a provider target.

    The target provider supplies the output schema. Canonical operations are
    eligible routing edges. Parameters are declared on the handler's model.

    Returns:
        A decorator preserving the directly callable synchronous handler.

    Raises:
        PluginDefinitionError: If unsupported options are supplied.
    """
    if options.keys() - {"canonical"}:
        message = "Unsupported location operation options"
        raise PluginDefinitionError(message)
    return _declare(
        name=name,
        code_revision=code_revision,
        description=description,
        outputs={},
        inputs=inputs,
        target=target,
        canonical=options.get("canonical", False),
    )


class _DeclarationOptions(TypedDict, total=False):
    target: str
    canonical: bool
    origins: Sequence[Origin]
    private: bool


@dataclass(frozen=True)
class _DeclarationSpec:
    name: str
    code_revision: str
    description: str
    outputs: Mapping[str, OutputSchema]
    inputs: Mapping[str, Dependency | LocationDependency]
    options: _DeclarationOptions


def _declare(
    *,
    name: str,
    code_revision: str,
    description: str,
    outputs: Mapping[str, OutputSchema],
    inputs: Mapping[str, Dependency | LocationDependency],
    **options: Unpack[_DeclarationOptions],
) -> Callable[[_HandlerT], _HandlerT]:
    spec = _DeclarationSpec(name, code_revision, description, outputs, inputs, options)

    def decorate(function: _HandlerT) -> _HandlerT:
        if hasattr(function, _DEFINITION_ATTRIBUTE):
            raise PluginDefinitionError(
                _definition_message(name, "handler is already decorated")
            )
        try:
            definition = _compile_definition(spec, function)
        except (ValueError, TypeError) as exc:
            raise PluginDefinitionError(_definition_message(name, str(exc))) from exc
        setattr(function, _DEFINITION_ATTRIBUTE, definition)
        return function

    return decorate


def _compile_definition(
    spec: _DeclarationSpec, function: Callable[..., object]
) -> NodeDefinition:
    validate_metric_name(spec.name)
    revision = TypeAdapter(ImplementationRevision).validate_python(spec.code_revision)
    if not isinstance(spec.description, str) or not spec.description.strip():
        message = "A nonblank description is required"
        raise ValueError(message)
    if any(
        not isinstance(value, Dependency | LocationDependency)
        for value in spec.inputs.values()
    ):
        message = "Inputs require live dependency declarations"
        raise TypeError(message)
    owned = deepcopy(dict(spec.inputs))
    target = spec.options.get("target")
    parameters, spatial, context = _handler_arguments(
        spec.name, function, owned, operation=target is not None
    )
    if parameters is not None:
        build_parameter_schema(spec.name, parameters)
    if spatial != {
        key for key, value in owned.items() if isinstance(value, LocationDependency)
    }:
        message = "Every location source must be declared in inputs"
        raise ValueError(message)
    compiled = {key: compile_output(schema) for key, schema in spec.outputs.items()}
    for output_name in compiled:
        validate_metric_name(output_name)
    definition = NodeDefinition(
        name=spec.name,
        kind="location_operation" if target is not None else "metric",
        function=function,
        parameters=parameters,
        accepts_context=context,
        code_revision=revision,
        description=spec.description,
        inputs=MappingProxyType(owned),
        schemas=MappingProxyType({key: value[0] for key, value in compiled.items()}),
        outputs=MappingProxyType({key: value[1] for key, value in compiled.items()}),
        resources=MappingProxyType(
            {
                key: requirement[1].name
                for key, annotation in _handler_hints(spec.name, function).items()
                if (requirement := resource_annotation(annotation)) is not None
            }
        ),
        target=target,
        origins=TypeAdapter(frozenset[Origin]).validate_python(
            spec.options.get("origins", ())
        ),
        private=spec.options.get("private", False),
        canonical=spec.options.get("canonical", False),
    )
    _validate_capabilities(definition)
    return definition


def _validate_capabilities(definition: NodeDefinition) -> None:
    if definition.kind == "metric":
        if not definition.outputs:
            message = "Metrics require at least one output"
            raise ValueError(message)
        return
    target = TypeAdapter(str).validate_python(definition.target, strict=True)
    sources = list(definition.inputs.values())
    if (
        not target
        or len(sources) != 1
        or not isinstance(sources[0], LocationDependency)
        or sources[0].provider is None
    ):
        message = "Operations require one provider source and a provider target"
        raise ValueError(message)
    if definition.resources or definition.origins:
        message = "Operations cannot declare resource bindings or direct origins"
        raise ValueError(message)


class PluginDefinition:
    """Explicit native nodes and registered resources, compiled without acquisition."""

    def __init__(
        self,
        *,
        metrics: Sequence[Callable[..., object]] = (),
        location_operations: Sequence[Callable[..., object]] = (),
        route_preferences: Sequence[Sequence[Callable[..., object]]] = (),
        resources: Mapping[str, type[BaseIO[Any]]] | None = None,
    ) -> None:
        """Capture declarations; combined catalogs resolve cross-plugin bindings.

        Raises:
            PluginDefinitionError: If registrations are malformed or duplicated.
        """
        declarations = {}
        if not metrics and not location_operations and not resources:
            msg = "PluginDefinition requires a node or resource"
            raise PluginDefinitionError(msg)
        for function, kind in [(f, "metric") for f in metrics] + [
            (f, "location_operation") for f in location_operations
        ]:
            definition = _registered_definition(function, kind)
            if definition.name in declarations:
                raise PluginDefinitionError(
                    _definition_message(definition.name, "duplicate node name")
                )
            declarations[definition.name] = definition
        providers = dict(resources or {})
        try:
            validate_resource_providers(providers)
        except ValueError as exc:
            raise PluginDefinitionError(str(exc)) from exc
        self.resources = MappingProxyType(providers)
        self._declarations = MappingProxyType(declarations)
        self._nodes: Mapping[str, NodeImplementation] | None = None
        self.route_preferences = tuple(
            tuple(_registered_definition(op, "location_operation").name for op in route)
            for route in route_preferences
        )

    @property
    def declarations(self) -> tuple[NodeDefinition, ...]:
        """Native executable declarations in deterministic order."""
        return tuple(self._declarations[name] for name in self.node_names)

    @property
    def is_bound(self) -> bool:
        """Whether combined catalog compilation has resolved the node bindings."""
        return self._nodes is not None

    def bind(
        self,
        nodes: Mapping[str, "CompiledNode"],
        providers: Mapping[str, "RegisteredProvider"],
    ) -> "PluginDefinition":
        """Return a detached definition with compiled location bindings."""
        implementations = {}
        for declaration in self.declarations:
            native = nodes[declaration.name]
            common: dict[str, Any] = {
                "name": declaration.name,
                "code_revision": declaration.code_revision,
                "description": declaration.description,
                "parameters_schema": build_parameter_schema(
                    declaration.name, declaration.parameters
                ),
                "location_inputs": {
                    key: LocationInputDeclaration(
                        contract=provider.contract,
                        provider=ProviderDeclaration(
                            resource=provider.resource,
                            contract=provider.contract,
                            description=provider.description,
                        ),
                        dependency=declaration.inputs[key],
                        description=_location_description(
                            declaration.name,
                            key,
                            _handler_hints(declaration.name, declaration.function)[key],
                        ),
                    )
                    for key, provider in native.locations.items()
                },
                "inputs": {
                    key: value
                    for key, value in declaration.inputs.items()
                    if isinstance(value, Dependency)
                },
                "resources": dict(declaration.resources),
            }
            location_schema = None
            if declaration.kind == "location_operation":
                target = providers[str(declaration.target)]
                location_schema = LocationOutputSchema(
                    target.schema.schema, id_column="location_id"
                )
                manifest = LocationOperationManifest(
                    **common,
                    canonical=declaration.canonical,
                    output_contract=target.contract,
                )
            else:
                manifest = MetricManifest(
                    **common,
                    outputs=dict(declaration.outputs),
                    origins=declaration.origins,
                    private=declaration.private,
                )
            implementations[declaration.name] = NodeImplementation(
                declaration.function,
                declaration.parameters,
                declaration.accepts_context,
                manifest,
                dict(declaration.schemas),
                location_schema,
                native_inputs=dict(declaration.inputs),
                target=declaration.target,
            )
        result: Self = copy(self)
        result._nodes = MappingProxyType(implementations)
        return result

    def validate_resources(self, providers: Mapping[str, type[BaseIO[Any]]]) -> None:
        """Validate handler resources against all active registrations.

        Raises:
            PluginDefinitionError: If a required resource is missing or incompatible.
        """
        for definition in self.declarations:
            for annotation in _handler_hints(
                definition.name, definition.function
            ).values():
                requirement = resource_annotation(annotation)
                if requirement is not None:
                    expected, marker = requirement
                    if marker.name not in providers or not issubclass(
                        providers[marker.name], expected
                    ):
                        msg = (
                            f"Node {definition.name!r}: "
                            f"Missing or incompatible resource: {marker.name}"
                        )
                        raise PluginDefinitionError(msg)

    @property
    def providers(self) -> tuple[ProviderDeclaration, ...]:
        """Provider metadata captured from declarations without resource acquisition."""
        return native_bindings.provider_declarations(self.resources)

    def implementation(self, name: str) -> NodeImplementation:
        """Return a detached handler descriptor with resolved provider contracts."""
        registered = self._get(name)
        return replace(
            registered,
            manifest=registered.manifest.model_copy(deep=True),
            schemas=dict(registered.schemas),
            location_schema=_owned_location_schema(registered.location_schema),
            native_inputs=(
                deepcopy(dict(registered.native_inputs))
                if registered.native_inputs is not None
                else None
            ),
        )

    def _get(self, name: str) -> NodeImplementation:
        if name not in self._declarations:
            raise PluginDefinitionError(
                _definition_message(
                    name, f"unknown node; available nodes: {', '.join(self.node_names)}"
                )
            )
        if self._nodes is None:
            return native_bindings.bind_self_contained(self).implementation(name)
        return self._nodes[name]

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Registered metric names in deterministic order."""
        return tuple(
            name
            for name in self.node_names
            if self._declarations[name].kind == "metric"
        )

    @property
    def location_operation_names(self) -> tuple[str, ...]:
        """Registered operation names in deterministic order."""
        return tuple(
            name
            for name in self.node_names
            if self._declarations[name].kind == "location_operation"
        )

    @property
    def node_names(self) -> tuple[str, ...]:
        """Registered node names in deterministic order."""
        return tuple(sorted(self._declarations))

    @property
    def node_manifests(self) -> tuple[NodeManifest, ...]:
        """Resolved node contracts for inspection and execution."""
        return tuple(
            self._get(name).manifest.model_copy(deep=True) for name in self.node_names
        )

    def describe(self, name: str) -> NodeDescription:
        """Return native node inspection metadata without resource acquisition."""
        definition = self._get(name)
        function = definition.function
        qualified_name = getattr(function, "__qualname__", type(function).__qualname__)
        function_name = getattr(function, "__name__", type(function).__name__)
        return NodeDescription(
            manifest=definition.manifest.model_copy(deep=True),
            handler=f"{function.__module__}.{qualified_name}",
            signature=f"{function_name}{inspect.signature(function)}",
        )

    def manifest(self, *, plugin: PluginInfo, factory: str) -> PluginManifest:
        """Return a serialized description of a self-contained or bound plugin."""
        return PluginManifest(
            route_preferences=self.route_preferences,
            plugin=plugin.model_copy(deep=True),
            factory=factory,
            providers=self.providers,
            resources={
                name: resource_metadata(provider)
                for name, provider in self.resources.items()
            },
            nodes=list(self.node_manifests),
        )


def _registered_definition(
    function: Callable[..., object], kind: str
) -> NodeDefinition:
    definition = getattr(function, _DEFINITION_ATTRIBUTE, None)
    if not isinstance(definition, NodeDefinition):
        msg = "PluginDefinition accepts only explicitly decorated handlers."
        raise PluginDefinitionError(msg)
    if definition.kind != kind:
        msg = f"Node {definition.name!r}: expected {kind} registration"
        raise PluginDefinitionError(msg)
    return definition
