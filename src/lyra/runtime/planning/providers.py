"""Acquisition-free public provider lookup over captured declarations."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.sdk.plugin import PluginDefinition


@dataclass(frozen=True)
class SelectedLocationProvider:
    """Provider ownership and resource lifecycle binding."""

    distribution: str
    declaration: ProviderDeclaration

    @property
    def resource(self) -> str:
        """Resource registration key."""
        return self.declaration.resource


def select_location_providers(
    registrations: Mapping[str, RegisteredProvider],
    definitions: Mapping[str, PluginDefinition],
) -> Mapping[str, SelectedLocationProvider]:
    """Associate each captured provider with its first registering distribution.

    Returns:
        Read-only discovery declarations indexed by provider ID.
    """
    owners: dict[str, str] = {}
    for distribution, definition in definitions.items():
        for key in definition.resources:
            owners.setdefault(key, distribution)
    return MappingProxyType(
        {
            name: SelectedLocationProvider(
                owners[provider.resource], provider.declaration
            )
            for name, provider in registrations.items()
        }
    )
