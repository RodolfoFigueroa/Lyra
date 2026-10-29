"""Typed parameter-model authoring and explicit plugin execution interfaces."""

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints

from jsonschema.exceptions import ValidationError as SchemaValidationError
from lyra.sdk.arguments import PreparedInvocation
from lyra.sdk.context import RunContext
from lyra.sdk.db import DatabaseIO
from lyra.sdk.dependencies import Dependency, DependencyManifest, Input
from lyra.sdk.errors import MetricInputError, PluginDefinitionError
from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
from lyra.sdk.models.output import OutputMetadata
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
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope
    from lyra.sdk.results import NativeOutputs

LocationInput = GeoJSON
BoundsInput = SingleGeoJSON
_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])
_DEFINITION_ATTRIBUTE = "__lyra_node_definition__"


class NodeDescription(StrictBaseModel):
    """Author-facing inspection of a handler and its canonical request contract."""

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


def _definition_message(name: str, detail: str) -> str:
    return f"Metric {name!r}: {detail}"


def _handler_arguments(
    name: str,
    function: Callable[..., object],
    inputs: Mapping[str, Dependency],
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
    reserved = {"parameters", "location", "bounds", "context"}
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
        if (
            key == "parameters"
            and isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
        ):
            parameters = annotation
        elif key == "location" and annotation is LocationInput:
            spatial[key] = "location"
        elif key == "bounds" and annotation is BoundsInput:
            spatial[key] = "bounds"
        elif key == "context" and annotation is RunContext:
            accepts_context = True
        elif key in inputs or resource_annotation(annotation) is not None:
            _validate_resource_argument(key, annotation, reserved, inputs)
        else:
            raise PluginDefinitionError(
                _definition_message(
                    name, f"{key}: unsupported name or missing/incompatible annotation"
                )
            )
    _validate_references(name, inputs, parameters)
    return parameters, spatial, accepts_context


def _validate_references(
    name: str, inputs: Mapping[str, Dependency], parameters: type[BaseModel] | None
) -> None:
    for dependency in inputs.values():
        for value in dependency.arguments.get("parameters", {}).values():
            if isinstance(value, Input) and (
                parameters is None
                or value.path.split(".")[1] not in parameters.model_fields
            ):
                raise PluginDefinitionError(
                    _definition_message(
                        name, f"undeclared input reference {value.path!r}"
                    )
                )


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
                name, function, inputs or {}
            )
            schema = build_request_schema(name, parameters, set(spatial))
            compiled = {key: compile_output(value) for key, value in outputs.items()}
            manifest = NodeManifest(
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


class PluginDefinition:
    """Explicitly registered handlers with local preparation and result checks."""

    def __init__(
        self,
        *,
        nodes: Sequence[Callable[..., object]],
        resources: Mapping[str, type[BaseIO]] | None = None,
    ) -> None:
        """Collect decorated functions without module scanning or service access.

        Raises:
            PluginDefinitionError: If handlers are absent, undecorated, or duplicate.
        """
        definitions: dict[str, _NodeDefinition] = {}
        if not nodes:
            msg = "PluginDefinition requires at least one decorated node."
            raise PluginDefinitionError(msg)
        for function in nodes:
            definition = getattr(function, _DEFINITION_ATTRIBUTE, None)
            if not isinstance(definition, _NodeDefinition):
                msg = "PluginDefinition accepts only explicitly decorated handlers."
                raise PluginDefinitionError(msg)
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
                        message = f"Missing or incompatible resource: {marker.name}"
                        raise PluginDefinitionError(message)
        self.resources = MappingProxyType(providers)
        self._nodes = MappingProxyType(definitions)

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
        return self.node_names

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
        """
        return validate_registered_result(
            name, self._get(name).schemas, value, lazy=lazy
        )

    def invoke(
        self,
        invocation: PreparedInvocation,
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
        definition = self._get(invocation.node)
        if dependencies.keys() != definition.manifest.inputs.keys():
            raise PluginDefinitionError(
                _definition_message(invocation.node, "dependency inputs do not match")
            )
        arguments = invocation.arguments() | dict(dependencies)
        if definition.accepts_context or definition.manifest.resources:
            context = context_factory(invocation.node)
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
                    return self.normalize_result(
                        invocation.node, definition.function(**arguments)
                    )
        return self.normalize_result(invocation.node, definition.function(**arguments))

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
        spatial: dict[str, GeoJSON | SingleGeoJSON] = {}
        for field in definition.manifest.spatial_inputs:
            model = GeoJSON if field == "location" else SingleGeoJSON
            try:
                spatial[field] = model.model_validate(inputs[field])
            except ValidationError as exc:
                raise MetricInputError(name, field, str(exc)) from exc
        return PreparedInvocation(name, parameters, spatial)


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
