"""Internal exact-ID acquisition for prepared native provider inputs."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import geopandas as gpd

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    ResourceConfigurationError,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.planning.provider_preparation import (
    PreparedProviderInput,
    ProviderSelection,
)
from lyra.runtime.resources import ResourceScope


class _ExactIDProvider(Protocol):
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame: ...


@dataclass(frozen=True)
class AcquiredProviderInput:
    """Native records retaining declaration identity outside mutable metadata."""

    frame: gpd.GeoDataFrame
    scope: ProviderScope


def acquire_provider_input(
    selection: PreparedProviderInput,
    resources: ResourceScope,
    *,
    needed_ids: Sequence[str] | None = None,
) -> AcquiredProviderInput:
    """Resolve a prepared selection or a needed subset in the caller's scope.

    The caller owns resource cleanup. No cache or cross-call content comparison
    participates in acquisition. Public providers and every execution entry point
    use this boundary.

    Returns:
        Validated native records with the captured provider scope.

    Raises:
        LocationSelectionError: If needed IDs are malformed or outside the selection.
        ResourceConfigurationError: If the resource binding cannot be configured.
        LocationAcquisitionError: If provider initialization or resolution fails.
        LocationProviderContractError: If output violates schema or exact membership.
    """
    registered = selection.provider
    try:
        parsed = ProviderSelection(
            provider=registered.scope.provider_id,
            ids=list(needed_ids) if needed_ids is not None else sorted(selection.ids),
        )
    except ValueError as exc:
        raise LocationSelectionError(selection.name, "Invalid needed IDs") from exc
    ids = list(dict.fromkeys(parsed.ids))
    if not set(ids).issubset(selection.ids):
        raise LocationSelectionError(
            selection.name, "Needed IDs must belong to the prepared selection", ids
        )
    if not ids:
        return AcquiredProviderInput(registered.schema.empty_frame(), registered.scope)
    try:
        provider = cast("_ExactIDProvider", resources.get(registered.resource))
        output = provider.resolve(list(ids))
    except ResourceConfigurationError:
        raise
    except Exception as exc:
        msg = (
            f"Location acquisition failed for provider {registered.scope.provider_id!r}"
        )
        raise LocationAcquisitionError(msg, ids=ids, input_name=selection.name) from exc
    try:
        frame = registered.schema.validate(output)
    except Exception as exc:
        raise LocationProviderContractError(
            selection.name, "Provider output failed its location schema", ids
        ) from exc
    returned = set(frame["location_id"])
    requested = set(ids)
    if returned != requested:
        raise LocationProviderContractError(
            selection.name,
            "Provider IDs must match requested IDs exactly",
            sorted(requested ^ returned),
        )
    return AcquiredProviderInput(frame, registered.scope)
