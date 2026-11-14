"""Internal exact-ID and candidate acquisition for native provider inputs."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import geopandas as gpd
import ibis.expr.types as ir

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    ResourceConfigurationError,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.native_inputs import copy_frame
from lyra.runtime.planning.provider_preparation import (
    PreparedProviderInput,
    ProviderSelection,
)
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.resources import ResourceScope

if TYPE_CHECKING:
    from lyra.runtime.provider_records import ProviderRecords
    from lyra.sdk.managed_provider import ManagedLocationProvider


class _ExactIDProvider(Protocol):
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame: ...


@dataclass(frozen=True)
class AcquiredProviderInput:
    """Native records retaining declaration identity outside mutable metadata."""

    frame: gpd.GeoDataFrame
    scope: ProviderScope


def acquire_provider_candidates(
    registered: RegisteredProvider,
    expression: ir.Table,
    resources: ResourceScope,
    *,
    records: "ProviderRecords | None" = None,
) -> AcquiredProviderInput:
    """Execute a validated selector and validate its complete candidate batch once.

    Returns:
        Authoritative candidates, including the declared empty shape.

    Raises:
        ResourceConfigurationError: If resource configuration is invalid.
        LocationAcquisitionError: If setup, execution, or decoding fails.
        LocationProviderContractError: If candidate records violate the schema.
    """
    try:
        provider = cast("ManagedLocationProvider", resources.get(registered.resource))
        with provider.operation("select"):
            output = provider.select(expression, registered.schema)
            frame = _validate_candidates(registered, output)
    except (ResourceConfigurationError, LocationProviderContractError):
        raise
    except Exception as exc:
        message = (
            "Candidate acquisition failed for provider "
            f"{registered.scope.provider_id!r}"
        )
        raise LocationAcquisitionError(message, input_name="candidates") from exc
    if records is not None:
        records.publish(registered, frame)
    return AcquiredProviderInput(copy_frame(frame), registered.scope)


def _validate_candidates(
    registered: RegisteredProvider, output: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    try:
        return registered.schema.validate(copy_frame(output))
    except Exception as exc:
        name = "candidates"
        raise LocationProviderContractError(
            name, "Provider output failed its location schema"
        ) from exc


def acquire_provider_input(
    selection: PreparedProviderInput,
    resources: ResourceScope,
    *,
    needed_ids: Sequence[str] | None = None,
    records: "ProviderRecords | None" = None,
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
        frame = registered.schema.validate(copy_frame(output))
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
    if records is not None:
        records.publish(registered, frame)
    return AcquiredProviderInput(copy_frame(frame), registered.scope)
