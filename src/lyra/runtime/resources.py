"""Resource acquisition, credential resolution and cleanup for one execution."""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.contracts.errors import ResourceConfigurationError
from lyra.contracts.resources import ResourceConfig, SecretReference
from lyra.sdk.resources import BaseIO, bind_reporting_context

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
                key: resolve_secret(settings.secrets[key])
                for key in provider.secret_names
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
    def activate(self) -> Generator[None, None, None]:
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
    registrations: Mapping[str, Mapping[str, type[BaseIO]]],
) -> dict[str, type[BaseIO]]:
    """Merge identical named declarations and reject conflicting providers.

    Returns:
        The combined registry of resource classes.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
    """
    providers: dict[str, type[BaseIO]] = {}
    for resources in registrations.values():
        for name, provider in resources.items():
            if name in providers and providers[name] is not provider:
                message = f"Conflicting resource declarations: {name}"
                raise ResourceConfigurationError(message)
            providers[name] = provider
    return providers
