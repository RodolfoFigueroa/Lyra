"""Shared provider acquisition into existing execution and storage bindings."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.resources import ResourceConfig
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.provider_acquisition import (
    acquire_provider_candidates,
    acquire_provider_input,
)
from lyra.runtime.resources import ResourceScope
from lyra.sdk.resources import BaseIO

if TYPE_CHECKING:
    from lyra.runtime.provider_records import ProviderRecords
    from lyra.runtime.report_collection import ReportCollection


class LocationResolver:
    """Borrow a resource scope and retain authoritative captured declarations."""

    def __init__(
        self,
        scope: ResourceScope,
        *,
        providers: Mapping[str, RegisteredProvider],
        provider_records: "ProviderRecords | None" = None,
        retained_providers: frozenset[str] = frozenset(),
        report: "ReportCollection | None" = None,
    ) -> None:
        """Bind metadata without constructing resources."""
        self.report = report
        self.provider_records = provider_records
        self.retained_providers = retained_providers | frozenset(
            name for name, provider in providers.items() if provider.cache
        )
        self.scope = scope
        self.providers = providers
        self.records: dict[str, NativeLocationInput] = {}

    def select(
        self, provider_id: str, expression: ir.Table, *, use_id: str | None = None
    ) -> NativeLocationInput:
        """Acquire complete query membership even when individual records are held.

        Returns:
            Validated candidates with their concrete target scope.
        """
        registered = self.providers[provider_id]
        acquired = acquire_provider_candidates(
            registered,
            expression,
            self.scope,
            records=self.provider_records
            if provider_id in self.retained_providers
            else None,
        )
        candidates = NativeLocationInput(acquired.frame, registered.contract.reference)
        self.retain(candidates)
        if self.report is not None:
            self.report.observe_provider(
                use_id, candidates.ids, executed=True, query=True
            )
        return candidates

    def retain(self, value: NativeLocationInput) -> None:
        """Reuse execution-owned provider records without another acquisition."""
        provider_id = value.contract.collection
        registered = self.providers.get(provider_id)
        if registered is None or registered.contract.reference != value.contract:
            return
        retained = self.records.get(provider_id)
        frame = value.frame.drop(
            columns="source_location_id", errors="ignore"
        ).drop_duplicates(subset=["location_id"])
        if retained is not None:
            frame = gpd.GeoDataFrame(
                pd.concat(
                    [retained.frame, frame[~frame.location_id.isin(retained.ids)]],
                    ignore_index=True,
                ),
                geometry="geometry",
                crs=frame.crs,
            )
        self.records[provider_id] = NativeLocationInput(frame, value.contract)

    def resolve_prepared(
        self,
        prepared: PreparedExecution,
        *,
        needed_ids: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, NativeLocationInput]:
        """Return accepted source batches under their receiving names.

        Raises:
            LocationSelectionError: If captured scope differs from the live binding.
            LocationAcquisitionError: If provider setup or resolution fails.
            LocationProviderContractError: If returned records violate the schema.
        """
        for item in prepared.inputs:
            registered = self.providers.get(item.binding.source.collection)
            if (
                registered is None
                or registered.contract.reference != item.binding.source
            ):
                raise LocationSelectionError(
                    item.name, "Prepared provider scope differs"
                )
        try:
            return self.resolve(prepared.request.inputs, needed_ids=needed_ids)
        except (
            LocationAcquisitionError,
            LocationSelectionError,
            LocationProviderContractError,
        ) as exc:
            exc.node = prepared.node
            raise

    def resolve(
        self,
        selections: Mapping[str, LocationSelection],
        *,
        needed_ids: Mapping[str, Sequence[str]] | None = None,
        use_ids: Mapping[str, str | None] | None = None,
    ) -> dict[str, NativeLocationInput]:
        """Validate bound selections and optional reduced demand before acquisition.

        Returns:
            Scoped runtime values containing every declared provider attribute.

        Raises:
            LocationSelectionError: If a selection or provider is invalid.
        """
        if needed_ids is not None and set(needed_ids) - set(selections):
            name = next(iter(set(needed_ids) - set(selections)))
            raise LocationSelectionError(name, "Unknown demanded input")
        prepared = []
        orders = {}
        for name, raw in selections.items():
            try:
                selection = LocationSelection.model_validate(raw.model_dump())
                registered = self.providers[selection.provider]
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise LocationSelectionError(
                    name, "Invalid provider selection"
                ) from exc
            demanded = _demanded_selection(
                name,
                selection,
                needed_ids.get(name) if needed_ids is not None else None,
            )
            orders[name] = list(dict.fromkeys(demanded.ids))
            prepared.append(
                PreparedProviderInput(name, frozenset(demanded.ids), registered)
            )
        values = {}
        for selection in prepared:
            provider_id = selection.provider.scope.provider_id
            retained = self.records.get(provider_id)
            missing = selection.ids - (set(retained.ids) if retained else set())
            if (
                missing
                and self.provider_records is not None
                and selection.provider.cache
            ):
                cached = self.provider_records.lookup(
                    selection.provider, sorted(missing)
                )
                self.retain(
                    NativeLocationInput(cached, selection.provider.contract.reference)
                )
                retained = self.records[provider_id]
                missing = selection.ids - set(retained.ids)
            use_id = use_ids.get(selection.name) if use_ids is not None else None
            if self.report is not None:
                self.report.observe_provider(
                    use_id, selection.ids - missing, executed=False
                )
            if retained is None or missing:
                acquired = acquire_provider_input(
                    selection,
                    self.scope,
                    needed_ids=sorted(missing),
                    records=self.provider_records
                    if provider_id in self.retained_providers
                    else None,
                )
                if self.report is not None:
                    self.report.observe_provider(use_id, missing, executed=True)
                frame = acquired.frame
                if retained is not None:
                    frame = gpd.GeoDataFrame(
                        pd.concat([retained.frame, frame], ignore_index=True),
                        geometry="geometry",
                        crs=frame.crs,
                    )
                retained = NativeLocationInput(
                    frame, selection.provider.contract.reference
                )
                self.records[provider_id] = retained
            ordered = (
                retained.frame.set_index("location_id", drop=False)
                .loc[orders[selection.name]]
                .reset_index(drop=True)
            )
            values[selection.name] = NativeLocationInput(ordered, retained.contract)
        return values


def _demanded_selection(
    name: str, selection: LocationSelection, needed: Sequence[str] | None
) -> LocationSelection:
    """Validate reduced demand without changing the original binding.

    Returns:
        A detached selection for the requested work.

    Raises:
        LocationSelectionError: If demand is invalid or exceeds the binding.
    """
    if needed is None:
        return selection
    try:
        demanded = LocationSelection(provider=selection.provider, ids=list(needed))
    except (ValueError, TypeError) as exc:
        raise LocationSelectionError(name, "Invalid needed IDs") from exc
    if not set(demanded.ids).issubset(selection.ids):
        raise LocationSelectionError(
            name, "Needed IDs must belong to the bound selection"
        )
    return demanded


def resolve_locations(
    selections: Mapping[str, LocationSelection],
    *,
    providers: Mapping[str, RegisteredProvider],
    resources: Mapping[str, type[BaseIO[Any]]],
    configuration: Mapping[str, ResourceConfig] | None = None,
    overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
) -> dict[str, NativeLocationInput]:
    """Return resolved records while owning resource initialization and cleanup.

    Raises:
        LocationAcquisitionError: If cleanup fails after successful acquisition.
    """
    scope = ResourceScope(resources, configuration, overrides)
    resolver = LocationResolver(scope, providers=providers)
    result = None
    try:
        with scope.activate():
            result = resolver.resolve(selections)
    except Exception as exc:
        if result is None:
            raise
        message = "Location resource cleanup failed"
        raise LocationAcquisitionError(message) from exc
    return result
