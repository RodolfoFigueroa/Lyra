"""Compile live single-collection registrations without resource acquisition.

This compiler is independent of request execution and manifest serialization.
Callers supply only active plugin definitions; schemas remain executable objects.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.locations import LocationContract, LocationFamily
from lyra.contracts.origins import Origin
from lyra.contracts.outputs import LocationOutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.resources import collect_resources
from lyra.sdk.locations import LocationProvider
from lyra.sdk.managed_provider import ManagedLocationProvider
from lyra.sdk.parquet import ParquetLocationProvider
from lyra.sdk.postgis import PostGISLocationProvider
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import ProviderQuery, compile_query_schema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.sdk.plugin import PluginDefinition


@dataclass(frozen=True)
class RegisteredProvider:
    """Captured identity and executable schema with its lifecycle binding."""

    resource: str
    scope: ProviderScope
    description: str | None
    schema: ProviderSchema
    query: ProviderQuery | None = None
    cache: bool = False
    adapter: Literal["parquet", "postgis"] | None = None
    membership_capable: bool = False

    @property
    def query_capable(self) -> bool:
        """Whether the provider class supplies managed query acquisition."""
        return self.query is not None

    @property
    def declaration(self) -> ProviderDeclaration:
        """Detached serializable discovery metadata."""
        return ProviderDeclaration(
            resource=self.resource,
            contract=self.contract,
            description=self.description,
            cache=self.cache,
            query_capable=self.query_capable,
            membership_capable=self.membership_capable,
            query_schema=self.query.metadata if self.query is not None else None,
        )

    @property
    def contract(self) -> LocationContract:
        """Existing execution binding retaining authoritative provider identity."""
        return LocationContract(
            family=LocationFamily(
                collection=self.scope.provider_id,
                unit_type="record",
                structure=LocationOutputMetadata(
                    id_column="location_id",
                    output=self.schema.metadata.model_copy(deep=True),
                ),
            ),
            source=self.scope.provider_id,
            geographical_edition="provider",
            meaning="Provider records",
            provider_scope=self.scope,
        )


def compile_provider_registry(
    definitions: Mapping[str, PluginDefinition],
) -> Mapping[str, RegisteredProvider]:
    """Index every provider resource in the supplied active plugins by public ID.

    Identical resource registrations coalesce across plugins, as in the ordinary
    resource registry. Distinct keys remain distinct registrations, even for the
    same class. Configuration and credentials are neither read nor resolved.

    Returns:
        Read-only lookup with metadata detached from caller-owned containers.

    Raises:
        PluginDefinitionError: If a provider declaration is invalid or ambiguous.
    """
    resources = collect_resources(
        {name: definition.resources for name, definition in definitions.items()}
    )
    for definition in definitions.values():
        definition.validate_resources(resources)
    result: dict[str, RegisteredProvider] = {}
    for resource, provider in resources.items():
        if not issubclass(provider, LocationProvider):
            continue
        captured = capture_provider(resource, provider)
        scope = captured.scope
        if scope.provider_id in result:
            previous = result[scope.provider_id].resource
            msg = (
                f"Conflicting provider ID {scope.provider_id!r}: "
                f"resources {previous!r} and {resource!r}"
            )
            raise PluginDefinitionError(msg)
        result[scope.provider_id] = captured
    return MappingProxyType(result)


def capture_provider(
    resource: str, provider: type[LocationProvider[Any]]
) -> RegisteredProvider:
    """Return captured provider metadata or a registration diagnostic.

    Raises:
        PluginDefinitionError: If the provider declaration is invalid.
    """
    try:
        return _capture_provider(resource, provider)
    except (AttributeError, TypeError, ValueError) as exc:
        message = f"Invalid provider registration {resource!r}: {exc}"
        raise PluginDefinitionError(message) from exc


def _capture_provider(
    resource: str, provider: type[LocationProvider[Any]]
) -> RegisteredProvider:
    """Capture an executable provider's schema and immutable declared identity.

    Returns:
        An acquisition-free registration description.

    Raises:
        ValueError: If the class is abstract or its declarations are invalid.
    """
    if inspect.isabstract(provider):
        msg = "Registered providers must be concrete"
        raise ValueError(msg)
    cache = TypeAdapter(bool).validate_python(provider.cache, strict=True)
    _validate_id_method(provider, "resolve")
    membership = inspect.getattr_static(provider, "existing_ids")
    membership_capable = (
        membership is not LocationProvider.existing_ids
        and not getattr(membership, "__isabstractmethod__", False)
    )
    if membership_capable:
        _validate_id_method(provider, "existing_ids")
    if issubclass(provider, PostGISLocationProvider):
        provider.validate_declaration()
    origins = getattr(provider, "origins", None)
    if not isinstance(origins, tuple | list | set | frozenset) or any(
        not isinstance(origin, Origin) for origin in origins
    ):
        msg = "Providers require an origins collection of Origin values"
        raise ValueError(msg)
    required = {name: getattr(provider, name) for name in ("id", "schema")}
    description = TypeAdapter(str | None).validate_python(
        getattr(provider, "description", None), strict=True
    )
    scope = ProviderScope(
        provider_id=required["id"],
        code_revision=getattr(provider, "code_revision", None),
        origins=origins,
        description=description,
    )
    schema = ProviderSchema(required["schema"])
    return RegisteredProvider(
        resource=resource,
        scope=scope,
        description=description,
        schema=schema,
        cache=cache,
        membership_capable=membership_capable,
        adapter="parquet"
        if issubclass(provider, ParquetLocationProvider)
        else "postgis"
        if issubclass(provider, PostGISLocationProvider)
        else None,
        query=compile_query_schema(schema.schema)
        if issubclass(provider, ManagedLocationProvider)
        else None,
    )


def _validate_id_method(provider: type[LocationProvider[Any]], name: str) -> None:
    resolver = inspect.getattr_static(provider, name)
    if (
        not inspect.isfunction(resolver)
        or inspect.iscoroutinefunction(resolver)
        or inspect.isasyncgenfunction(resolver)
        or inspect.isgeneratorfunction(resolver)
    ):
        message = f"Provider {name} must be a synchronous, nongenerator instance method"
        raise ValueError(message)
    parameters = tuple(inspect.signature(resolver).parameters.values())
    if len(parameters) != 2 or any(
        argument.kind
        not in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        or argument.default is not inspect.Parameter.empty
        for argument in parameters
    ):
        message = (
            f"Provider {name} must accept only self and ids as positional arguments"
        )
        raise ValueError(message)
