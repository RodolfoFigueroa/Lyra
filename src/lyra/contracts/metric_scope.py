"""Metric ownership and cache eligibility derived from receiving declarations."""

from collections.abc import Mapping
from typing import Literal

from lyra.contracts.dependencies import (
    Dependency,
    LocationDependency,
    validate_bindings,
)

CachePolicy = Literal["off", "full", "per_location"]
OutputScope = Literal["location", "unpartitioned"]


def primary_input(inputs: Mapping[str, Dependency | LocationDependency]) -> str | None:
    """Return the sole declared owner after validating nested bindings.

    Raises:
        ValueError: If multiple owners or nested ownership overrides are declared.
    """
    owners = []
    for name, dependency in inputs.items():
        validate_bindings(dict(dependency.locations), "locations")
        if isinstance(dependency, LocationDependency) and dependency.primary:
            owners.append(name)
    if len(owners) > 1:
        msg = "Metrics permit only one primary location input"
        raise ValueError(msg)
    return owners[0] if owners else None


def validate_cache(cache: CachePolicy, scope: OutputScope) -> None:
    """Validate the exact policy spelling and scope eligibility.

    Raises:
        ValueError: If the policy is malformed or ineligible for the scope.
    """
    if not isinstance(cache, str) or cache not in {"off", "full", "per_location"}:
        msg = "cache must be 'off', 'full', or 'per_location'"
        raise ValueError(msg)
    if cache == "per_location" and scope == "unpartitioned":
        msg = "Unpartitioned metrics cannot use per_location caching"
        raise ValueError(msg)
