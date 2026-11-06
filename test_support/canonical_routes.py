"""Reusable canonical routes fixtures."""

from lyra.contracts.locations import LocationContract
from lyra.runtime.planning.catalog import (
    CanonicalEdge,
)
from test_support.canonical_operations import selector


def contract(name: str) -> LocationContract:
    return (
        selector()
        .location_inputs["source"]
        .contract.model_copy(update={"source": name})
    )


def edges(pairs: list[tuple[str, str]]) -> tuple[CanonicalEdge, ...]:
    return tuple(
        CanonicalEdge(f"op_{i}", "source", contract(a), contract(b))
        for i, (a, b) in enumerate(pairs)
    )
