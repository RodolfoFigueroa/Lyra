"""Acquisition-free native request preparation for managed runtime integration."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from lyra.contracts.locations import LocationSelection
from lyra.runtime.planning.provider_registry import RegisteredProvider


@dataclass(frozen=True)
class PreparedProviderInput:
    """Detached selection retaining scope even when no records are needed."""

    name: str
    ids: frozenset[str]
    provider: RegisteredProvider


def validate_provider_selections(
    selections: Mapping[str, LocationSelection],
    required: Mapping[str, str],
    accepted: Mapping[str, Collection[str]],
) -> None:
    """Validate caller-supplied provider inputs before any acquisition.

    Raises:
        ValueError: If input names or selected providers are unsupported.
    """
    if selections.keys() != required.keys():
        message = f"Expected inputs {sorted(required)}"
        raise ValueError(message)
    for name, selection in selections.items():
        if selection.provider not in accepted[name]:
            message = (
                f"Input {name!r} requires provider {required[name]!r} "
                "or a declared route"
            )
            raise ValueError(message)
