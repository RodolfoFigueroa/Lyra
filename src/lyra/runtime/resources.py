"""Resource acquisition, credential resolution and cleanup for one execution."""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.contracts.errors import ResourceConfigurationError
from lyra.contracts.resources import ResourceConfig, SecretReference
from lyra.sdk.resources import (
    BaseIO,
    Resource,
    bind_reporting_context,
    resource_dependencies,
    validate_resource_providers,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from lyra.sdk.context import RunContext

_active_scope: ContextVar[ResourceScope | None] = ContextVar("io_scope", default=None)


def resolve_secret(reference: SecretReference) -> str:
    """Read a credential without exposing its value in diagnostics.

    Returns:
        The nonblank credential value.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
    """
    try:
        value = (
            os.environ[reference.env]
            if reference.env is not None
            else Path(str(reference.file)).read_text(encoding="utf-8")
        )
    except (KeyError, OSError, ValueError):
        message = "Cannot resolve resource credential"
        raise ResourceConfigurationError(message) from None
    if not value.strip():
        message = "Empty resource credential"
        raise ResourceConfigurationError(message)
    return value.strip()


class ResourceScope:
    """Own lazily initialized resources for a single execution."""

    def __init__(
        self,
        providers: Mapping[str, type[BaseIO[Any]]],
        configuration: Mapping[str, ResourceConfig] | None = None,
        overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
    ) -> None:
        """Bind providers and deployment configuration without accessing services.

        Raises:
            ResourceConfigurationError: If resource configuration is invalid.
        """
        try:
            validate_resource_providers(providers)
            validate_resource_providers(overrides or {})
        except ValueError as exc:
            raise ResourceConfigurationError(str(exc)) from exc
        self.providers = dict(providers)
        for name, provider in (overrides or {}).items():
            if name in self.providers and not issubclass(
                provider, self.providers[name]
            ):
                message = f"Incompatible resource override: {name}"
                raise ResourceConfigurationError(message)
            self.providers[name] = provider
        self.dependencies = validate_resource_graph(self.providers)
        self.configuration = configuration or {}
        self.instances: dict[str, BaseIO[Any]] = {}
        self._ready: set[str] = set()
        self._active = False
        self._closed = False

    def get(self, name: str) -> BaseIO[Any]:
        """Initialize and return a named resource once.

        Returns:
            The instance owned by this execution.

        Raises:
            ResourceConfigurationError: If configuration is invalid, initialization
                did not complete, or the scope is closing or closed.
        """
        if self._closed:
            message = "Resource scope is closing or closed"
            raise ResourceConfigurationError(message)
        if name in self.instances:
            if name not in self._ready:
                message = f"Resource {name!r} did not complete initialization"
                raise ResourceConfigurationError(message)
            return self.instances[name]
        provider = self.providers.get(name)
        if provider is None:
            message = f"Resource {name!r} is not registered"
            raise ResourceConfigurationError(message)
        dependencies = {
            attribute: self.get(marker.name)
            for attribute, (_, marker) in self.dependencies[name].items()
        }
        settings = self.configuration.get(name, ResourceConfig())
        try:
            config = provider.config_model.model_validate(settings.config)
            secrets = {
                key: resolve_secret(settings.secrets[key])
                for key in provider.secret_names
            }
        except (ValueError, KeyError, ResourceConfigurationError):
            message = f"Invalid configuration or credentials for resource {name!r}"
            raise ResourceConfigurationError(message) from None
        instance = provider(config, secrets)
        self.instances[name] = instance
        instance.resource_name = name
        for attribute, dependency in dependencies.items():
            setattr(instance, attribute, dependency)
        instance.setup()
        self._ready.add(name)
        return instance

    @contextmanager
    def activate(self) -> Generator[None, None, None]:
        """Activate this scope and attempt all cleanup on exit.

        Raises:
            ResourceConfigurationError: If already active or closed.
        """
        if self._active or self._closed:
            message = "Resource scope is already active or closed"
            raise ResourceConfigurationError(message)
        self._active = True
        token = _active_scope.set(self)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._closed = True
            try:
                with ExitStack() as cleanup:
                    for instance in self.instances.values():
                        cleanup.callback(instance.close)
                    self.instances.clear()
                    self._ready.clear()
                    if failed:
                        # Even a cleanup interrupt must preserve the primary error.
                        with suppress(BaseException):
                            cleanup.close()
            finally:
                self._active = False
                _active_scope.reset(token)


@contextmanager
def resource_invocation(context: RunContext) -> Generator[ResourceScope, None, None]:
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
    with bind_reporting_context(context):
        yield scope


def collect_resources(
    registrations: Mapping[str, Mapping[str, type[BaseIO[Any]]]],
) -> dict[str, type[BaseIO[Any]]]:
    """Merge identical named declarations and reject conflicting providers.

    Returns:
        The combined registry of resource classes.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
    """
    providers: dict[str, type[BaseIO[Any]]] = {}
    for resources in registrations.values():
        for name, provider in resources.items():
            if name in providers and providers[name] is not provider:
                message = f"Conflicting resource declarations: {name}"
                raise ResourceConfigurationError(message)
            providers[name] = provider
    validate_resource_graph(providers)
    return providers


def validate_resource_graph(
    providers: Mapping[str, type[BaseIO[Any]]],
) -> dict[str, dict[str, tuple[type[BaseIO[Any]], Resource]]]:
    """Validate every registered dependency without constructing resources.

    Returns:
        Effective attribute requirements for each registration.

    Raises:
        ResourceConfigurationError: If declarations, bindings or cycles are invalid.
    """
    try:
        validate_resource_providers(providers)
        requirements = {
            name: resource_dependencies(provider)
            for name, provider in providers.items()
        }
    except ValueError as exc:
        raise ResourceConfigurationError(str(exc)) from exc
    graph: dict[str, set[str]] = {}
    for name, attributes in requirements.items():
        graph[name] = set()
        for attribute, (expected, marker) in attributes.items():
            registered = providers.get(marker.name)
            if registered is None or not issubclass(registered, expected):
                message = (
                    f"Resource {name!r}.{attribute}: "
                    f"Missing or incompatible resource: {marker.name}"
                )
                raise ResourceConfigurationError(message)
            graph[name].add(marker.name)
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        message = f"Resource dependency cycle: {exc.args[1]}"
        raise ResourceConfigurationError(message) from exc
    return requirements
