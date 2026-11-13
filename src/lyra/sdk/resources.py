"""Resource declarations and operation reporting for plugin authors."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel
from typing_extensions import TypeVar

from lyra.contracts.resources import IOConfig

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from lyra.sdk.context import RunContext

ConfigT = TypeVar("ConfigT", bound=BaseModel, default=IOConfig)
_active_context: ContextVar[RunContext | None] = ContextVar("io_context", default=None)


@contextmanager
def bind_reporting_context(context: RunContext) -> Generator[None, None, None]:
    """Attribute resource operations in this block to the supplied context."""
    token = _active_context.set(context)
    try:
        yield
    finally:
        _active_context.reset(token)


@dataclass(frozen=True)
class Resource:
    """Identify the deployment-wide binding requested by an annotated argument."""

    name: str


class BaseIO(Generic[ConfigT]):
    """Base for synchronous resource implementations owned by an execution.

    Plain subclasses use IOConfig. For custom configuration, specify both the
    generic argument to type self.config and config_model for runtime validation.
    Subclasses inherit each independently.
    """

    config_model: type[BaseModel] = IOConfig
    secret_names: tuple[str, ...] = ()

    def __init__(self, config: ConfigT, secrets: Mapping[str, str]) -> None:
        """Capture configuration; initialize external clients in setup."""
        self.config = config
        self.secrets = MappingProxyType(dict(secrets))
        self.resource_name = type(self).__name__

    def setup(self) -> None:
        """Initialize clients when the resource is first requested."""

    def close(self) -> None:
        """Release clients, including after partial setup failure."""

    @contextmanager
    def operation(self, name: str) -> Generator[None, None, None]:
        """Record a completed synchronous operation without changing its outcome."""
        context = _active_context.get()
        started = time.monotonic()
        error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            error = exc
            raise
        finally:
            fields = {
                "resource": self.resource_name,
                "operation": name,
                "duration_seconds": time.monotonic() - started,
                "outcome": "success" if error is None else "failure",
                "exception_class": type(error).__name__ if error is not None else None,
                "job_id": context.job_id if context else None,
                "node": context.node if context else None,
            }
            with suppress(Exception):
                logger = context.logger if context else logging.getLogger("lyra.io")
                if isinstance(logger, logging.LoggerAdapter):
                    logger = logger.logger
                logger.info(
                    "Resource operation completed", extra={"structured_fields": fields}
                )


def resource_annotation(
    annotation: object,
) -> tuple[type[BaseIO[Any]], Resource] | None:
    """Extract a typed resource requirement from an Annotated parameter.

    Returns:
        The resource type and binding marker, or None for ordinary arguments.

    Raises:
        ValueError: If resource configuration is invalid.
    """
    if get_origin(annotation) is not Annotated:
        return None
    target, *metadata = get_args(annotation)
    markers = [item for item in metadata if isinstance(item, Resource)]
    if not markers:
        return None
    if (
        len(markers) != 1
        or not isinstance(target, type)
        or not issubclass(target, BaseIO)
        or not _valid_binding_name(markers[0].name)
    ):
        message = "Resource arguments require a BaseIO type and one named Resource"
        raise ValueError(message)
    return target, markers[0]


def resource_metadata(resource: type[BaseIO[Any]]) -> dict[str, object]:
    """Describe a provider without constructing it or resolving credentials.

    Returns:
        Serializable provider identity, configuration schema, and secret names.
    """
    return {
        "type": f"{resource.__module__}:{resource.__qualname__}",
        "config_schema": resource.config_model.model_json_schema(),
        "secrets": list(resource.secret_names),
    }


def resource_dependencies(
    resource: type[BaseIO[Any]],
) -> dict[str, tuple[type[BaseIO[Any]], Resource]]:
    """Inspect effective inherited attribute requirements without acquisition.

    Returns:
        Attribute names mapped to required types and registration markers.

    Raises:
        ValueError: If an annotation cannot be resolved or is malformed.
    """
    try:
        hints = get_type_hints(resource, include_extras=True)
    except (NameError, TypeError) as exc:
        message = f"Cannot resolve resource annotations for {resource.__name__}: {exc}"
        raise ValueError(message) from exc
    requirements = {}
    for name, annotation in hints.items():
        try:
            requirement = resource_annotation(annotation)
        except ValueError as exc:
            message = f"Resource {resource.__name__}.{name}: {exc}"
            raise ValueError(message) from exc
        if requirement is not None:
            requirements[name] = requirement
    return requirements


def validate_resource_providers(providers: Mapping[str, type[BaseIO[Any]]]) -> None:
    """Validate registration names, resource classes, and attribute declarations.

    Raises:
        ValueError: If a registration or annotation is invalid.
    """
    for name, provider in providers.items():
        if (
            not _valid_binding_name(name)
            or not isinstance(provider, type)
            or not issubclass(provider, BaseIO)
        ):
            message = f"Invalid resource declaration: {name!r}"
            raise ValueError(message)
        resource_dependencies(provider)


def _valid_binding_name(name: object) -> bool:
    return isinstance(name, str) and bool(name.strip()) and name == name.strip()
