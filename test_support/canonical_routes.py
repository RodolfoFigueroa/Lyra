"""Reusable canonical routes fixtures."""

from lyra.contracts.locations import LocationContract
from lyra.runtime.planning.catalog import (
    CanonicalEdge,
)
from test_support.canonical_operations import selector


def contract(name: str) -> LocationContract:
    result = selector().location_inputs["source"].contract.model_copy(deep=True)
    result.family = result.family.model_copy(update={"collection": name})
    result.source = name
    assert result.provider_scope is not None
    result.provider_scope = result.provider_scope.model_copy(
        update={"provider_id": name}
    )
    return result


def edges(pairs: list[tuple[str, str]]) -> tuple[CanonicalEdge, ...]:
    return tuple(
        CanonicalEdge(f"op_{i}", "source", contract(a), contract(b))
        for i, (a, b) in enumerate(pairs)
    )
