"""Typed parameter-model authoring and explicit plugin execution interfaces."""

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

from jsonschema.exceptions import ValidationError as SchemaValidationError
from lyra.sdk.arguments import PreparedInvocation, PreparedResolvedInvocation
from lyra.sdk.context import LocationContext, RunContext
from lyra.sdk.db import DatabaseIO
from lyra.sdk.dependencies import (
    Dependency,
    DependencyManifest,
    Input,
    LocationDependency,
)
from lyra.sdk.errors import MetricInputError, PluginDefinitionError
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider, LocationProviderManifest
from lyra.sdk.models.location import Locations
from lyra.sdk.models.output import LocationOutputMetadata, OutputMetadata
from lyra.sdk.models.plugin import (
    NodeManifest,
    PluginInfo,
    PluginManifest,
    SpatialInputKind,
    validate_metric_name,
)
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.parameters import build_request_schema
from lyra.sdk.resources import (
    BaseIO,
    ResourceConfig,
    ResourceScope,
    resource_annotation,
    resource_invocation,
    resource_metadata,
)
from lyra.sdk.results import validate_registered_result
from lyra.sdk.schema import check_json_value, subschema_validator
from pydantic import BaseModel, Field, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticSerializationError

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope
    from lyra.sdk.results import NativeOutputs

_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])
_DEFINITION_ATTRIBUTE = "__lyra_node_definition__"


class NodeDescription(StrictBaseModel):
    """Author-facing inspection of a handler and its canonical request contract."""

    kind: Literal["metric", "location_operation"]
    location_output: LocationOutputMetadata | None
    name: str
    description: str
    handler: str
    signature: str
    request_schema: dict[str, Any]
    spatial_inputs: dict[str, SpatialInputKind]
    inputs: dict[str, DependencyManifest]
    outputs: dict[str, OutputMetadata]
    resources: dict[str, str]


@dataclass(frozen=True)
class _NodeDefinition:
    function: Callable[..., object]
    parameters: type[BaseModel] | None
    accepts_context: bool
    manifest: NodeManifest
    schemas: dict[str, Any]
    location_schema: LocationOutputSchema | None = None
    requires_resolver: bool = False


def _definition_message(name: str, detail: str) -> str:
    return f"Metric {name!r}: {detail}"


def _handler_arguments(
    name: str,
    function: Callable[..., object],
    inputs: Mapping[str, Dependency],
    *,
    operation: bool = False,
) -> tuple[type[BaseModel] | None, dict[str, SpatialInputKind], bool]:
    if (
        not inspect.isfunction(function)
        or inspect.iscoroutinefunction(function)
        or inspect.isgeneratorfunction(function)
        or inspect.isasyncgenfunction(function)
    ):
        raise PluginDefinitionError(
            _definition_message(
                name, "handler must be a synchronous, nongenerator function"
            )
        )
    reserved = {"parameters", "context"}
    if (
        reserved & inputs.keys()
        or inputs.keys() - inspect.signature(function).parameters.keys()
    ):
        raise PluginDefinitionError(
            _definition_message(name, "invalid dependency input keys")
        )
    hints = _handler_hints(name, function)
    parameters: type[BaseModel] | None = None
    spatial: dict[str, SpatialInputKind] = {}
    accepts_context = False
    for argument in inspect.signature(function).parameters.values():
        _validate_argument_kind(name, argument)
        key = argument.name
        annotation = hints.get(key)
        location_type = (
            get_args(annotation)[0]
            if get_origin(annotation) is Annotated
            else annotation
        )
        if location_type is Locations and (key in reserved or key in inputs):
            raise PluginDefinitionError(
                _definition_message(
                    name, f"{key}: location argument conflicts with another input"
                )
            )
        if (
            key == "parameters"
            and isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
        ):
            parameters = annotation
        elif location_type is Locations:
            _location_description(name, key, annotation)
            spatial[key] = "location"
        elif key == "context" and (
            annotation is RunContext or (operation and annotation is LocationContext)
        ):
            accepts_context = True
        elif key in inputs or resource_annotation(annotation) is not None:
            _validate_resource_argument(key, annotation, reserved, inputs)
        else:
            raise PluginDefinitionError(
                _definition_message(
                    name, f"{key}: unsupported name or missing/incompatible annotation"
                )
            )
    _validate_references(name, inputs, parameters, spatial)
    return parameters, spatial, accepts_context


def _location_description(name: str, key: str, annotation: object) -> str | None:
    if get_origin(annotation) is not Annotated:
        return None
    metadata = get_args(annotation)[1:]
    if len(metadata) != 1 or not isinstance(metadata[0], FieldInfo):
        raise PluginDefinitionError(
            _definition_message(
                name, f"{key}: use Annotated[Locations, Field(description=...)]"
            )
        )
    field = metadata[0]
    if (
        field.asdict() != Field(description=field.description).asdict()
        or not isinstance(field.description, str)
        or not field.description.strip()
    ):
        raise PluginDefinitionError(
            _definition_message(
                name,
                f"{key}: location metadata supports only a nonblank Field description",
            )
        )
    return field.description


def _validate_references(
    name: str,
    inputs: Mapping[str, Dependency],
    parameters: type[BaseModel] | None,
    spatial: Mapping[str, SpatialInputKind],
) -> None:
    available = {
        "parameters": {f"parameters.{field}" for field in parameters.model_fields}
        if parameters is not None
        else set(),
        "locations": set(spatial),
    }

    def validate(dependency: Dependency | LocationDependency, path: str) -> None:
        for category, bindings in (
            ("locations", dependency.locations),
            ("parameters", dependency.parameters),
        ):
            for receiving, binding in bindings.items():
                if isinstance(binding, LocationDependency):
                    validate(
                        binding, f"{path}.locations.{receiving} ({binding.operation})"
                    )
                if (
                    isinstance(binding, Input)
                    and binding.path not in available[category]
                ):
                    raise PluginDefinitionError(
                        _definition_message(
                            name,
                            f"dependency {path}.{category}.{receiving}: "
                            f"undeclared input reference {binding.path!r} "
                            f"for {category}",
                        )
                    )

    for input_name, dependency in inputs.items():
        validate(dependency, f"{input_name!r}, output {dependency.output!r}")


def _handler_hints(name: str, function: Callable[..., object]) -> dict[str, Any]:
    try:
        # Return annotations are documentation only; do not require resolving them.
        annotations = {
            key: value
            for key, value in function.__annotations__.items()
            if key != "return"
        }
        annotation_holder = type(
            "HandlerAnnotations", (), {"__annotations__": annotations}
        )
        hints = get_type_hints(
            annotation_holder,
            globalns=getattr(function, "__globals__", {}),
            include_extras=True,
        )
    except (NameError, TypeError) as exc:
        raise PluginDefinitionError(
            _definition_message(name, f"unresolved handler annotation: {exc}")
        ) from exc
    return hints


def _validate_argument_kind(name: str, argument: inspect.Parameter) -> None:
    if argument.kind not in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }:
        raise PluginDefinitionError(
            _definition_message(name, f"{argument.name}: unsupported argument kind")
        )
    if argument.default is not inspect.Parameter.empty:
        raise PluginDefinitionError(
            _definition_message(
                name,
                f"{argument.name}: handler defaults are unsupported; use model fields",
            )
        )


def _node(
    *,
    name: str,
    description: str,
    outputs: Mapping[str, OutputSchema],
    inputs: Mapping[str, Dependency] | None,
    location_schema: LocationOutputSchema | None = None,
) -> Callable[[_HandlerT], _HandlerT]:
    """Register one contract while preserving the directly callable function.

    Returns:
        A decorator that validates and records the metric's definition.
    """

    def decorate(function: _HandlerT) -> _HandlerT:
        if hasattr(function, _DEFINITION_ATTRIBUTE):
            raise PluginDefinitionError(
                _definition_message(name, "handler is already decorated")
            )
        if any(not isinstance(value, Dependency) for value in (inputs or {}).values()):
            raise PluginDefinitionError(
                _definition_message(name, "inputs must contain Dependency declarations")
            )
        try:
            validate_metric_name(name)
            parameters, spatial, context = _handler_arguments(
                name, function, inputs or {}, operation=location_schema is not None
            )
            schema = build_request_schema(
                name,
                parameters,
                set(spatial),
                descriptions={
                    key: _location_description(
                        name, key, _handler_hints(name, function)[key]
                    )
                    for key in spatial
                },
            )
            compiled = {key: compile_output(value) for key, value in outputs.items()}
            manifest = NodeManifest(
                kind="location_operation" if location_schema else "metric",
                location_output=location_schema.metadata if location_schema else None,
                name=name,
                description=description,
                request_schema=schema,
                spatial_inputs=spatial,
                inputs={
                    key: DependencyManifest.from_declaration(value)
                    for key, value in (inputs or {}).items()
                },
                outputs={key: value[1] for key, value in compiled.items()},
                resources={
                    key: requirement[1].name
                    for key, annotation in _handler_hints(name, function).items()
                    if (requirement := resource_annotation(annotation)) is not None
                },
            )
        except (TypeError, ValueError) as exc:
            raise PluginDefinitionError(_definition_message(name, str(exc))) from exc
        setattr(
            function,
            _DEFINITION_ATTRIBUTE,
            _NodeDefinition(
                function,
                parameters,
                context,
                manifest.model_copy(deep=True),
                {key: value[0] for key, value in compiled.items()},
                location_schema,
                _handler_hints(name, function).get("context") is LocationContext,
            ),
        )
        return function

    return decorate


def metric(
    *,
    name: str,
    description: str,
    outputs: Mapping[str, OutputSchema],
    inputs: Mapping[str, Dependency] | None = None,
) -> Callable[[_HandlerT], _HandlerT]:
    """Declare a public metric, preserving the original callable.

    Returns:
        The shared node decorator for a metric.
    """
    return _node(
        name=name,
        description=description,
        outputs=outputs,
        inputs=inputs,
    )


def location_operation(
    *,
    name: str,
    description: str,
    output: OutputSchema | LocationOutputSchema | None = None,
    inputs: Mapping[str, Dependency] | None = None,
) -> Callable[[_HandlerT], _HandlerT]:
    """Declare a synchronous operation returning one schema-validated Locations.

    Use LocationOutputSchema to specify custom ID and geometry column names.
    Register with PluginDefinition(location_operations=[...]) and bind the
    result with LocationDependency. Inputs declare ordinary table dependencies.
    A LocationContext supplies managed resolution; RunContext supplies reporting.
    Handlers receive whole location sets. Constructed entities use a distinct
    scope and do not register provider collections.

    Graph execution validates output and applies declared schema coercion,
    including to empty and cached results. Direct function calls omit these checks.

    Returns:
        A decorator preserving the original directly callable function.
    """

    def decorate(function: _HandlerT) -> _HandlerT:
        if output is None:
            msg = f"Location operation {name!r}: output schema is required"
            raise PluginDefinitionError(msg)
        try:
            contract = (
                output
                if isinstance(output, LocationOutputSchema)
                else LocationOutputSchema(output)
            )
            return _node(
                name=name,
                description=description,
                outputs={},
                inputs=inputs,
                location_schema=contract,
            )(function)
        except (TypeError, ValueError, PluginDefinitionError) as exc:
            msg = f"Location operation {name!r}: {exc}"
            raise PluginDefinitionError(msg) from exc

    return decorate


class PluginDefinition:
    """Explicitly registered handlers with local preparation and result checks."""

    def __init__(
        self,
        *,
        metrics: Sequence[Callable[..., object]] = (),
        location_operations: Sequence[Callable[..., object]] = (),
        resources: Mapping[str, type[BaseIO]] | None = None,
        location_provider: str | None = None,
    ) -> None:
        """Collect decorated functions without module scanning or service access.

        Raises:
            PluginDefinitionError: If the plugin is empty, handlers are invalid,
                or the designated provider is missing or incompatible.
        """
        definitions: dict[str, _NodeDefinition] = {}
        if not metrics and not location_operations and location_provider is None:
            msg = "PluginDefinition requires a decorated node or location provider."
            raise PluginDefinitionError(msg)
        registrations = [(f, "metric") for f in metrics] + [
            (f, "location_operation") for f in location_operations
        ]
        for function, kind in registrations:
            definition = _registered_definition(function, kind)
            name = definition.manifest.name
            if name in definitions:
                raise PluginDefinitionError(
                    _definition_message(name, "duplicate node name")
                )
            definitions[name] = definition
        providers = dict(resources or {})
        _validate_resource_providers(providers)
        if any(
            "database" in d.manifest.resources.values() for d in definitions.values()
        ):
            providers.setdefault("database", DatabaseIO)
        for definition in definitions.values():
            for annotation in _handler_hints(
                definition.manifest.name, definition.function
            ).values():
                requirement = resource_annotation(annotation)
                if requirement is not None:
                    expected, marker = requirement
                    if marker.name not in providers or not issubclass(
                        providers[marker.name], expected
                    ):
                        message = (
                            f"Node {definition.manifest.name!r}: "
                            f"Missing or incompatible resource: {marker.name}"
                        )
                        raise PluginDefinitionError(message)
        self._location_provider = _provider_manifest(providers, location_provider)
        self.resources = MappingProxyType(providers)
        self._nodes = MappingProxyType(definitions)

    @property
    def location_provider(self) -> LocationProviderManifest | None:
        """Independent provider metadata, available without resource acquisition."""
        return (
            self._location_provider.model_copy(deep=True)
            if self._location_provider is not None
            else None
        )

    def _get(self, name: str) -> _NodeDefinition:
        try:
            return self._nodes[name]
        except KeyError as exc:
            available = ", ".join(self.node_names)
            raise PluginDefinitionError(
                _definition_message(
                    name,
                    f"unknown node; available nodes: {available}",
                )
            ) from exc

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Registered metric names in deterministic order."""
        return tuple(
            name
            for name in self.node_names
            if self._nodes[name].manifest.kind == "metric"
        )

    @property
    def location_operation_names(self) -> tuple[str, ...]:
        """Registered location operation names in deterministic order."""
        return tuple(
            name
            for name in self.node_names
            if self._nodes[name].manifest.kind == "location_operation"
        )

    @property
    def node_names(self) -> tuple[str, ...]:
        """All registered node names in deterministic order."""
        return tuple(sorted(self._nodes))

    @property
    def node_manifests(self) -> tuple[NodeManifest, ...]:
        """Independent copies of the registered node contracts."""
        return tuple(
            self._nodes[name].manifest.model_copy(deep=True) for name in self.node_names
        )

    def describe(self, name: str) -> NodeDescription:
        """Return inspection metadata without adding it to the persisted manifest."""
        definition = self._get(name)
        function = definition.function
        qualified_name = getattr(function, "__qualname__", type(function).__qualname__)
        name = getattr(function, "__name__", type(function).__name__)
        return NodeDescription(
            **definition.manifest.model_dump(),
            handler=f"{function.__module__}.{qualified_name}",
            signature=f"{name}{inspect.signature(function)}",
        )

    def manifest(self, *, plugin: PluginInfo, factory: str) -> PluginManifest:
        """Return the persisted local node contract, without runtime state."""
        return PluginManifest(
            plugin=plugin.model_copy(deep=True),
            factory=factory,
            location_provider=self.location_provider,
            resources={
                name: resource_metadata(provider)
                for name, provider in self.resources.items()
            },
            nodes=[
                self._nodes[name].manifest.model_copy(deep=True)
                for name in self.node_names
            ],
        )

    def prepare_parameters(self, name: str, value: object) -> BaseModel:
        """Validate parameter JSON before parsing it through the author's model.

        Returns:
            The typed model, including validated defaults and semantic checks.

        Raises:
            PluginDefinitionError: If the metric declares no parameter model.
            MetricInputError: If the input is not valid JSON or fails validation.
        """
        definition = self._get(name)
        model = definition.parameters
        if model is None:
            raise PluginDefinitionError(
                _definition_message(name, "metric has no parameters")
            )
        schema = definition.manifest.request_schema
        properties = schema["properties"]
        if not isinstance(properties, dict) or not isinstance(
            properties["parameters"], dict
        ):
            raise PluginDefinitionError(
                _definition_message(name, "parameters schema is missing")
            )
        try:
            check_json_value(value, "parameters")
            encoded = json.dumps(value, allow_nan=False)
            subschema_validator(schema, properties["parameters"]).validate(value)
            return model.model_validate_json(encoded)
        except SchemaValidationError as exc:
            path = ".".join(["parameters", *(str(item) for item in exc.path)])
            raise MetricInputError(name, path, exc.message) from exc
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            path = ".".join(["parameters", *(str(item) for item in error["loc"])])
            raise MetricInputError(name, path, error["msg"]) from exc
        except (TypeError, ValueError) as exc:
            raise MetricInputError(name, "parameters", str(exc)) from exc

    def normalize_result(
        self, name: str, value: object, *, lazy: bool = False
    ) -> "NativeOutputs":
        """Check a node result and validate its named native tables.

        Returns:
            Checked outputs for the requested metric.

        Raises:
            PluginDefinitionError: If the node is not a metric.
        """
        definition = self._get(name)
        if definition.manifest.kind != "metric":
            msg = f"Node {name!r} is not a metric"
            raise PluginDefinitionError(msg)
        return validate_registered_result(name, definition.schemas, value, lazy=lazy)

    def normalize_location_result(
        self, name: str, value: object, *, lazy: bool = False
    ) -> Locations:
        """Validate one operation result and return schema-coerced locations.

        Returns:
            Validated locations with declared coercion applied.

        Raises:
            PluginDefinitionError: If the name is not an operation.
            ValueError: If the operation result violates its contract.
            TypeError: If the result is not Locations.
        """
        contract = self._get(name).location_schema
        if contract is None:
            msg = f"Node {name!r} is not a location operation"
            raise PluginDefinitionError(msg)
        if not isinstance(value, Locations):
            msg = f"Location operation {name!r}: result must be one Locations value"
            raise TypeError(msg)
        try:
            return contract.validate(value, lazy=lazy)
        except Exception as exc:
            msg = f"Location operation {name!r}: invalid result: {exc}"
            raise ValueError(msg) from exc

    def invoke_operation(
        self,
        invocation: PreparedInvocation | PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> Locations:
        """Invoke a declared operation with supplied dependencies and context.

        Returns:
            The validated, schema-coerced location result.

        Raises:
            PluginDefinitionError: If the node is not a location operation.
        """
        if self._get(invocation.node).manifest.kind != "location_operation":
            msg = f"Node {invocation.node!r} is not a location operation"
            raise PluginDefinitionError(msg)
        return self.normalize_location_result(
            invocation.node,
            self._invoke_handler(invocation, dependencies, context_factory),
        )

    def invoke(
        self,
        invocation: PreparedInvocation | PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> "NativeOutputs":
        """Invoke one prepared node with resolved tables and check its outputs.

        Returns:
            All validated native outputs.

        Raises:
            PluginDefinitionError: If inputs or the runtime context violate the
                node contract.
        """
        if self._get(invocation.node).manifest.kind != "metric":
            msg = f"Node {invocation.node!r} is not a metric"
            raise PluginDefinitionError(msg)
        return self.normalize_result(
            invocation.node,
            self._invoke_handler(invocation, dependencies, context_factory),
        )

    def _invoke_handler(
        self,
        invocation: PreparedInvocation | PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> object:
        definition = self._get(invocation.node)
        if dependencies.keys() != definition.manifest.inputs.keys():
            raise PluginDefinitionError(
                _definition_message(invocation.node, "dependency inputs do not match")
            )
        arguments = invocation.arguments() | dict(dependencies)
        if definition.accepts_context or definition.manifest.resources:
            context = context_factory(invocation.node)
            if definition.requires_resolver and not callable(
                getattr(context, "resolve_locations", None)
            ):
                msg = (
                    f"Location operation {invocation.node!r}: "
                    "context requires resolve_locations"
                )
                raise PluginDefinitionError(msg)
            if definition.accepts_context:
                arguments["context"] = context
            if definition.manifest.resources:
                with resource_invocation(context) as scope:
                    arguments.update(
                        {
                            key: scope.get(name)
                            for key, name in definition.manifest.resources.items()
                        }
                    )
                    return definition.function(**arguments)
        return definition.function(**arguments)

    def __call__(
        self,
        job: "JobEnvelope",
        context: RunContext,
        *,
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO]] | None = None,
    ) -> object:
        """Prepare resolved inputs and invoke a handler, returning its native value.

        Returns:
            The validated handler result.

        Raises:
            PluginDefinitionError: If graph execution is required.
        """
        definition = self._get(job.metric)
        if definition.manifest.kind != "metric":
            msg = f"Unknown public metric: {job.metric!r}"
            raise PluginDefinitionError(msg)
        if definition.manifest.inputs:
            raise PluginDefinitionError(
                _definition_message(
                    job.metric,
                    "this job adapter requires a metric without dependencies",
                )
            )
        arguments = self.prepare_invocation(job.metric, job.input).arguments()
        if definition.accepts_context:
            arguments["context"] = context
        with (
            ResourceScope(
                self.resources, resource_config, resource_overrides
            ).activate(),
            resource_invocation(context) as scope,
        ):
            arguments.update(
                {
                    key: scope.get(name)
                    for key, name in definition.manifest.resources.items()
                }
            )
            outputs = self.normalize_result(
                job.metric, definition.function(**arguments)
            )
            return next(iter(outputs.values())) if len(outputs) == 1 else outputs

    def prepare_resolved_invocation(
        self, name: str, inputs: Mapping[str, object]
    ) -> PreparedResolvedInvocation:
        """Validate effective parameters and scoped locations without acquisition.

        Returns:
            Inputs ready for graph execution, retaining full resolved scope.

        Raises:
            MetricInputError: If fields or scoped location values are invalid.
        """
        definition = self._get(name)
        expected = set(definition.manifest.spatial_inputs)
        if definition.parameters is not None:
            expected.add("parameters")
        if set(inputs) != expected:
            raise MetricInputError(name, "input", f"expected fields {sorted(expected)}")
        parameters = (
            self.prepare_parameters(name, inputs["parameters"])
            if definition.parameters is not None
            else None
        )
        locations: dict[str, Locations] = {}
        for field in definition.manifest.spatial_inputs:
            try:
                value = inputs[field]
                if isinstance(value, Locations):
                    Locations.model_validate(value.model_dump(warnings="error"))
                locations[field] = Locations.model_validate(value)
            except (ValidationError, PydanticSerializationError) as exc:
                raise MetricInputError(name, field, str(exc)) from exc
        return PreparedResolvedInvocation(
            name, parameters, locations, kind=definition.manifest.kind
        )

    def prepare_invocation(
        self, name: str, inputs: Mapping[str, object]
    ) -> PreparedInvocation:
        """Validate ordinary inputs with already-resolved spatial values.

        Returns:
            Effective parameters and native spatial models without executing nodes.

        Raises:
            MetricInputError: If inputs do not match the node's contract.
        """
        definition = self._get(name)
        expected = set(definition.manifest.spatial_inputs)
        if definition.parameters is not None:
            expected.add("parameters")
        if set(inputs) != expected:
            raise MetricInputError(name, "input", f"expected fields {sorted(expected)}")
        parameters = (
            self.prepare_parameters(name, inputs["parameters"])
            if definition.parameters is not None
            else None
        )
        spatial: dict[str, Locations] = {}
        for field in definition.manifest.spatial_inputs:
            try:
                value = inputs[field]
                if isinstance(value, Locations):
                    Locations.model_validate(value.model_dump(warnings="error"))
                spatial[field] = Locations.model_validate(value)
            except (ValidationError, PydanticSerializationError) as exc:
                raise MetricInputError(name, field, str(exc)) from exc
        return PreparedInvocation(
            name, parameters, spatial, kind=definition.manifest.kind
        )


def _validate_resource_argument(
    key: str, annotation: object, reserved: set[str], inputs: Mapping[str, Dependency]
) -> None:
    if resource_annotation(annotation) is not None and (
        key in reserved or key in inputs
    ):
        message = "Resource argument conflicts with another input"
        raise PluginDefinitionError(message)


def _validate_resource_providers(providers: Mapping[str, type[BaseIO]]) -> None:
    for name, provider in providers.items():
        if (
            not name.strip()
            or name != name.strip()
            or not isinstance(provider, type)
            or not issubclass(provider, BaseIO)
        ):
            message = f"Invalid resource declaration: {name!r}"
            raise PluginDefinitionError(message)
        if name == "database" and provider is not DatabaseIO:
            message = "The database binding is supplied by the runtime"
            raise PluginDefinitionError(message)


def _provider_manifest(
    resources: Mapping[str, type[BaseIO]], name: str | None
) -> LocationProviderManifest | None:
    if name is None:
        return None
    provider = resources.get(name)
    if (
        provider is None
        or not issubclass(provider, LocationProvider)
        or inspect.isabstract(provider)
    ):
        message = f"Missing or incompatible location provider resource: {name!r}"
        raise PluginDefinitionError(message)
    return LocationProviderManifest(
        resource=name,
        collections=tuple(
            item.model_copy(
                update={
                    "outputs": {
                        unit: provider.outputs[item.name, unit].metadata
                        for unit in item.unit_types
                    }
                }
            )
            for item in provider.collections
        ),
    )


def _registered_definition(
    function: Callable[..., object], kind: str
) -> _NodeDefinition:
    definition = getattr(function, _DEFINITION_ATTRIBUTE, None)
    if not isinstance(definition, _NodeDefinition):
        msg = "PluginDefinition accepts only explicitly decorated handlers."
        raise PluginDefinitionError(msg)
    if definition.manifest.kind != kind:
        msg = f"Node {definition.manifest.name!r}: expected {kind} registration"
        raise PluginDefinitionError(msg)
    return definition
