"""Acquisition-free public provider lookup over captured declarations."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import TypeAdapter

from lyra.contracts.providers import ProviderDeclaration


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
    declarations: Mapping[str, Sequence[ProviderDeclaration]],
) -> Mapping[str, SelectedLocationProvider]:
    """Return unique registrations indexed by public provider ID.

    Raises:
        ValueError: If two distinct registrations claim one public ID.
    """
    selected = {}
    for distribution, registrations in declarations.items():
        for declaration in registrations:
            TypeAdapter(bool).validate_python(declaration.cache, strict=True)
            identifier = declaration.contract.family.collection
            existing = selected.get(identifier)
            if existing is not None:
                if existing.declaration != declaration:
                    message = f"Conflicting provider ID {identifier!r}"
                    raise ValueError(message)
                continue
            selected[identifier] = SelectedLocationProvider(distribution, declaration)
    return MappingProxyType(selected)
