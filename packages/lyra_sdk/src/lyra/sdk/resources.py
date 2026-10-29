"""Synchronous resources shared by the nodes of one execution."""

from __future__ import annotations

import logging
import os
import time
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Generic, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from lyra.sdk.context import RunContext
    from lyra.sdk.plugin import PluginDefinition

ConfigT = TypeVar("ConfigT", bound=BaseModel)
_active_context: ContextVar[RunContext | None] = ContextVar("io_context", default=None)
_active_scope: ContextVar[ResourceScope | None] = ContextVar("io_scope", default=None)


class IOConfig(BaseModel):
    """Configuration for a resource, empty unless extended by its author."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SecretReference(IOConfig):
    """Locate a credential in an environment variable or UTF-8 file."""

    env: str | None = Field(
        default=None, description="Environment variable containing the credential."
    )
    file: Path | None = Field(
        default=None, description="UTF-8 file containing the credential."
    )

    @model_validator(mode="after")
    def validate_source(self) -> SecretReference:
        """Require exactly one credential source.

        Returns:
            This reference with exactly one source.

        Raises:
            ValueError: If resource configuration is invalid.
        """
        if (self.env is None) == (self.file is None):
            message = "Specify exactly one of env or file"
            raise ValueError(message)
        return self

    def resolve(self) -> str:
        """Read a credential without exposing its value in diagnostics.

        Returns:
            The nonblank credential value.

        Raises:
            ResourceConfigurationError: If resource configuration is invalid.
        """
        try:
            value = (
                os.environ[self.env]
                if self.env is not None
                else Path(str(self.file)).read_text(encoding="utf-8")
            )
        except (KeyError, OSError, ValueError):
            message = "Cannot resolve resource credential"
            raise ResourceConfigurationError(message) from None
        if not value.strip():
            message = "Empty resource credential"
            raise ResourceConfigurationError(message)
        return value.strip()


class ResourceConfig(IOConfig):
    """Deployment configuration for one named resource."""

    config: dict[str, object] = Field(
        default_factory=dict,
        description="Values validated against the resource configuration model.",
    )
    secrets: dict[str, SecretReference] = Field(
        default_factory=dict,
        description="Named credential references resolved during execution.",
    )


class ResourceConfigurationError(RuntimeError):
    """A resource could not be configured for execution."""

    input_name: str | None = None


@dataclass(frozen=True)
class Resource:
    """Identify the deployment-wide binding requested by an annotated argument."""

    name: str


class BaseIO(Generic[ConfigT]):
    """Base for synchronous resource implementations owned by an execution."""

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
    def operation(self, name: str) -> Iterator[None]:
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
                "exception_class": type(error).__name__ if error else None,
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


def resource_annotation(annotation: object) -> tuple[type[BaseIO], Resource] | None:
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
        or not markers[0].name.strip()
    ):
        message = "Resource arguments require a BaseIO type and one named Resource"
        raise ValueError(message)
    return target, markers[0]


def resource_metadata(resource: type[BaseIO]) -> dict[str, object]:
    """Describe a provider without constructing it or resolving credentials.

    Returns:
        Serializable provider identity, configuration schema, and secret names.
    """
    return {
        "type": f"{resource.__module__}:{resource.__qualname__}",
        "config_schema": resource.config_model.model_json_schema(),
        "secrets": list(resource.secret_names),
    }


class ResourceScope:
    """Own lazily initialized resources for a single execution."""

    def __init__(
        self,
        providers: Mapping[str, type[BaseIO]],
        configuration: Mapping[str, ResourceConfig] | None = None,
        overrides: Mapping[str, type[BaseIO]] | None = None,
    ) -> None:
        """Bind providers and deployment configuration without accessing services.

        Raises:
            ResourceConfigurationError: If resource configuration is invalid.
        """
        self.providers = dict(providers)
        for name, provider in (overrides or {}).items():
            if name in self.providers and not issubclass(
                provider, self.providers[name]
            ):
                message = f"Incompatible resource override: {name}"
                raise ResourceConfigurationError(message)
            self.providers[name] = provider
        self.configuration = configuration or {}
        self.instances: dict[str, BaseIO] = {}

    def get(self, name: str) -> BaseIO:
        """Initialize and return a named resource once.

        Returns:
            The instance owned by this execution.

        Raises:
            ResourceConfigurationError: If resource configuration is invalid.
        """
        if name in self.instances:
            return self.instances[name]
        provider = self.providers.get(name)
        if provider is None:
            message = f"Resource {name!r} is not registered"
            raise ResourceConfigurationError(message)
        settings = self.configuration.get(name, ResourceConfig())
        try:
            config = provider.config_model.model_validate(settings.config)
            secrets = {
                key: settings.secrets[key].resolve() for key in provider.secret_names
            }
        except (ValueError, KeyError, ResourceConfigurationError):
            message = f"Invalid configuration or credentials for resource {name!r}"
            raise ResourceConfigurationError(message) from None
        instance = provider(config, secrets)
        instance.resource_name = name
        self.instances[name] = instance
        instance.setup()
        return instance

    @contextmanager
    def activate(self) -> Iterator[None]:
        """Activate this scope and attempt all cleanup on exit."""
        token = _active_scope.set(self)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            try:
                with ExitStack() as cleanup:
                    for instance in self.instances.values():
                        cleanup.callback(instance.close)
                    self.instances.clear()
                    if failed:
                        with suppress(Exception):
                            cleanup.close()
            finally:
                _active_scope.reset(token)


@contextmanager
def resource_invocation(context: RunContext) -> Iterator[ResourceScope]:
    """Bind node attribution within the active resource scope.

    Yields:
        The active resource scope.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
    """
    scope = _active_scope.get()
    if scope is None:
        message = "Resource invocation requires an execution scope"
        raise ResourceConfigurationError(message)
    token = _active_context.set(context)
    try:
        yield scope
    finally:
        _active_context.reset(token)


def collect_resources(
    plugins: Mapping[str, PluginDefinition],
) -> dict[str, type[BaseIO]]:
    """Merge identical named declarations and reject conflicting providers.

    Returns:
        The combined registry of resource classes.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
    """
    providers: dict[str, type[BaseIO]] = {}
    for plugin in plugins.values():
        for name, provider in plugin.resources.items():
            if name in providers and providers[name] is not provider:
                message = f"Conflicting resource declarations: {name}"
                raise ResourceConfigurationError(message)
            providers[name] = provider
    return providers
