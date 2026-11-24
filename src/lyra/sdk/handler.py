"""Inspection of synchronous native node handlers without invoking them."""

import inspect
from collections.abc import Callable, Mapping
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from geopandas import GeoDataFrame
from ibis.expr.types import Table
from pydantic import Field
from pydantic.fields import FieldInfo

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.sdk.context import RunContext
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.resources import resource_annotation


def _definition_message(name: str, detail: str) -> str:
    return f"Metric {name!r}: {detail}"


def _handler_arguments(
    name: str,
    function: Callable[..., object],
    inputs: Mapping[str, Dependency | LocationDependency],
) -> tuple[type[MetricParameters] | None, set[str], bool]:
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
    parameters: type[MetricParameters] | None = None
    spatial = {
        key for key, value in inputs.items() if isinstance(value, LocationDependency)
    }
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
        if (
            key == "parameters"
            and isinstance(annotation, type)
            and issubclass(annotation, MetricParameters)
        ):
            parameters = annotation
        elif key in spatial:
            if location_type is not GeoDataFrame:
                raise PluginDefinitionError(
                    _definition_message(
                        name, f"{key}: location inputs require GeoDataFrame"
                    )
                )
            _location_description(name, key, annotation)
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
    _validate_references(name, inputs, parameters, spatial)
    return parameters, spatial, accepts_context


def _location_description(name: str, key: str, annotation: object) -> str | None:
    if get_origin(annotation) is not Annotated:
        return None
    metadata = get_args(annotation)[1:]
    if len(metadata) != 1 or not isinstance(metadata[0], FieldInfo):
        raise PluginDefinitionError(
            _definition_message(
                name, f"{key}: use Annotated[GeoDataFrame, Field(description=...)]"
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
    inputs: Mapping[str, Dependency | LocationDependency],
    parameters: type[MetricParameters] | None,
    spatial: set[str],
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
        validate(
            dependency,
            f"{input_name!r}, output {dependency.output!r}"
            if isinstance(dependency, Dependency)
            else repr(input_name),
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


def _validate_resource_argument(
    key: str,
    annotation: object,
    reserved: set[str],
    inputs: Mapping[str, Dependency | LocationDependency],
) -> None:
    if resource_annotation(annotation) is not None and (
        key in reserved or key in inputs
    ):
        msg = "Resource argument conflicts with another input"
        raise PluginDefinitionError(msg)


def _operation_arguments(
    name: str,
    function: Callable[..., object],
    target_selector: Callable[..., object] | None = None,
) -> type[MetricParameters] | None:
    """Validate mapping and selector slots and their shared parameter model.

    Returns:
        The optional shared parameter model inferred from the handler.

    Raises:
        PluginDefinitionError: If callback slots or parameter models are invalid.
    """
    model = _operation_callback_arguments(
        name,
        function,
        (GeoDataFrame,) if target_selector is None else (GeoDataFrame, GeoDataFrame),
    )
    if target_selector is not None:
        selector_model = _operation_callback_arguments(
            name, target_selector, (GeoDataFrame, Table)
        )
        if selector_model is not model:
            raise PluginDefinitionError(
                _definition_message(
                    name, "selector and handler must declare the same parameters model"
                )
            )
    return model


def _operation_callback_arguments(
    name: str, function: Callable[..., object], table_types: tuple[type, ...]
) -> type[MetricParameters] | None:
    """Inspect the positional computational callback without executing it.

    Returns:
        The optional parameter model.

    Raises:
        PluginDefinitionError: If the callback exposes unsupported arguments.
    """
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
    arguments = list(inspect.signature(function).parameters.values())
    hints = _handler_hints(name, function)
    count = len(table_types)
    if not count <= len(arguments) <= count + 1:
        raise PluginDefinitionError(
            _definition_message(
                name, f"callback requires {count} positional table slots"
            )
        )
    for argument, expected in zip(arguments, table_types, strict=False):
        annotation = hints.get(argument.name)
        table_type = (
            get_args(annotation)[0]
            if get_origin(annotation) is Annotated
            else annotation
        )
        if (
            argument.kind
            not in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
            or argument.default is not inspect.Parameter.empty
            or table_type is not expected
        ):
            raise PluginDefinitionError(
                _definition_message(
                    name, f"{argument.name} must be a positional {expected.__name__}"
                )
            )
        if expected is GeoDataFrame:
            _location_description(name, argument.name, annotation)
        elif annotation is not expected:
            raise PluginDefinitionError(
                _definition_message(name, "Ibis table slots do not accept metadata")
            )
    if len(arguments) == count:
        return None
    argument = arguments[count]
    model = hints.get(argument.name)
    if (
        argument.name != "parameters"
        or argument.kind is not inspect.Parameter.KEYWORD_ONLY
        or argument.default is not inspect.Parameter.empty
        or not isinstance(model, type)
        or not issubclass(model, MetricParameters)
    ):
        raise PluginDefinitionError(
            _definition_message(
                name,
                "only keyword-only parameters with a MetricParameters "
                "subclass annotation are supported",
            )
        )
    return model
