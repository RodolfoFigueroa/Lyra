"""Bind native declarations into the existing graph's executable descriptors."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from lyra.contracts.dependencies import LocationDependency
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning import node_declarations, provider_registry
from lyra.sdk.locations import LocationProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.resources import BaseIO


def provider_declarations(
    resources: Mapping[str, type[BaseIO[Any]]],
) -> tuple[ProviderDeclaration, ...]:
    """Return detached provider descriptions without resolving resource dependencies."""
    return tuple(
        ProviderDeclaration(
            resource=name, contract=captured.contract, description=captured.description
        )
        for name, provider in resources.items()
        if issubclass(provider, LocationProvider)
        for captured in (provider_registry.capture_provider(name, provider),)
    )


def bind_self_contained(definition: PluginDefinition) -> PluginDefinition:
    """Bind local provider metadata while retaining external metric dependencies.

    Returns:
        A service-free description with executable local schemas.

    Raises:
        ValueError: If a location input has no locally registered schema.
    """
    if definition.is_bound:
        return definition
    providers = provider_registry.compile_provider_registry({"local": definition})
    declarations = {value.name: value for value in definition.declarations}
    compiled = {}
    for value in declarations.values():
        locations = {}
        for key, binding in value.inputs.items():
            if not isinstance(binding, LocationDependency):
                continue
            provider_id = binding.provider
            if provider_id is None:
                operation = declarations.get(str(binding.operation))
                if operation is None or operation.kind != "location_operation":
                    message = f"Unknown location operation {binding.operation!r}"
                    raise ValueError(message)
                provider_id = operation.target
            if provider_id is None or provider_id not in providers:
                message = f"Unknown provider {provider_id!r}"
                raise ValueError(message)
            locations[key] = providers[provider_id]
        compiled[value.name] = node_declarations.CompiledNode(
            node_declarations.NodeDeclaration(
                value.name,
                value.function,
                value.inputs,
                tuple(value.outputs),
                value.kind,
                value.target,
                tuple(value.origins),
            ),
            MappingProxyType(locations),
            value.origins,
            value.parameters,
        )
    return definition.bind(compiled, providers)


def bind_native_definitions(
    definitions: Mapping[str, PluginDefinition],
) -> Mapping[str, PluginDefinition]:
    """Compile all active provider requirements before acquisition or execution.

    Returns:
        Detached executable definitions sharing the combined provider catalog.
    """
    if all(definition.is_bound for definition in definitions.values()):
        return definitions
    registry = provider_registry.compile_provider_registry(definitions)
    declarations = [
        node_declarations.NodeDeclaration(
            name=value.name,
            function=value.function,
            inputs=value.inputs,
            outputs=tuple(value.outputs),
            kind=value.kind,
            target=value.target,
            origins=tuple(value.origins),
        )
        for definition in definitions.values()
        for value in definition.declarations
    ]
    compiled = node_declarations.compile_node_declarations(definitions, declarations)
    return MappingProxyType(
        {
            owner: definition.bind(compiled, registry)
            for owner, definition in definitions.items()
        }
    )
