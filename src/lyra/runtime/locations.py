"""Shared provider acquisition into existing execution and storage bindings."""

from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import KW_ONLY, InitVar, dataclass, field
from typing import TYPE_CHECKING, Any, cast

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.locations import LocationContractReference, LocationSelection
from lyra.contracts.provider_scope import RecordReferences
from lyra.contracts.resources import ResourceConfig
from lyra.data.intervention_records import decode_additions
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.persistence import persistence_boundary
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.provider_acquisition import (
    CatalogChanges,
    acquire_provider_candidates,
    acquire_provider_input,
)
from lyra.runtime.resources import ResourceScope
from lyra.sdk.resources import BaseIO

if TYPE_CHECKING:
    from lyra.contracts.interventions import AcceptedIntervention
    from lyra.runtime.provider_records import ProviderRecords
    from lyra.runtime.report_collection import ReportCollection


@dataclass(eq=False)
class LocationResolver:
    """Borrow a resource scope and retain authoritative captured declarations."""

    scope: ResourceScope
    _: KW_ONLY
    providers: Mapping[str, RegisteredProvider]
    provider_records: "ProviderRecords | None" = None
    retained_providers: frozenset[str] = frozenset()
    report: "ReportCollection | None" = None
    interventions: InitVar[Sequence["AcceptedIntervention"]] = ()
    records: dict[str, NativeLocationInput] = field(default_factory=dict, init=False)
    _changes: dict[str, CatalogChanges] = field(init=False)

    def __post_init__(self, interventions: Sequence["AcceptedIntervention"]) -> None:
        """Bind validated request values without constructing resources."""
        self.retained_providers |= frozenset(
            name for name, provider in self.providers.items() if provider.cache
        )
        self._changes = {
            change.catalog: CatalogChanges(
                change,
                change.effective_scope(self.providers[change.catalog].scope),
                {cast("str", row["location_id"]): row for row in change.add},
                frozenset(change.remove),
            )
            for change in interventions
        }

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
            changes=self._changes.get(provider_id),
        )
        self._publish_records(
            registered,
            acquired.frame,
            RecordReferences(acquired.scope, acquired.added_ids),
        )
        candidates = NativeLocationInput(
            acquired.frame, self._contract(registered), added_ids=acquired.added_ids
        )
        self.retain(candidates)
        if self.report is not None:
            self.report.observe_provider(
                use_id,
                set(candidates.ids) - candidates.added_ids,
                executed=True,
                query=True,
            )
        return candidates

    def retain(self, value: NativeLocationInput) -> None:
        """Reuse execution-owned provider records without another acquisition."""
        provider_id = value.contract.provider_id
        registered = self.providers.get(provider_id)
        if registered is None or self._contract(registered) != value.contract:
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
        added = value.added_ids | retained.added_ids if retained else value.added_ids
        self.records[provider_id] = NativeLocationInput(
            frame, value.contract, added_ids=added
        )

    def _contract(self, provider: RegisteredProvider) -> LocationContractReference:
        change = self._changes.get(provider.scope.provider_id)
        reference = provider.contract.reference
        return (
            reference.model_copy(update={"provider_scope": change.scope})
            if change
            else reference
        )

    def check_removals(self, name: str, selection: LocationSelection) -> None:
        """Reject explicitly removed IDs, including in a skipped input.

        Raises:
            LocationSelectionError: If any selected ID is unavailable due to removal.
        """
        change = self._changes.get(selection.provider)
        if change and (removed := change.removals.intersection(selection.ids)):
            raise LocationSelectionError(
                name,
                "Requested IDs were removed from the effective dataset",
                sorted(removed),
            )

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
        scopes = prepared.required_scopes()
        for item in prepared.inputs:
            registered = self.providers.get(item.binding.source)
            if (
                registered is None
                or registered.scope
                != scopes[item.binding.source].contract.provider_scope
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
        lineages: Mapping[str, object] | None = None,
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
        for name, raw in selections.items():
            try:
                selection = LocationSelection.model_validate(raw.model_dump())
                registered = self.providers[selection.provider]
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise LocationSelectionError(
                    name, "Invalid provider selection"
                ) from exc
            self.check_removals(name, selection)
            demanded = _demanded_selection(
                name,
                selection,
                needed_ids.get(name) if needed_ids is not None else None,
            )
            ordered = list(dict.fromkeys(demanded.ids))
            prepared.append(
                (PreparedProviderInput(name, frozenset(ordered), registered), ordered)
            )
        return {
            selection.name: self._resolve_ids(
                selection,
                ordered,
                use_ids.get(selection.name) if use_ids is not None else None,
                lineages[selection.name] if lineages is not None else object(),
            )
            for selection, ordered in prepared
        }

    def _resolve_ids(
        self,
        selection: PreparedProviderInput,
        order: list[str],
        use_id: str | None,
        lineage: object,
    ) -> NativeLocationInput:
        provider = selection.provider
        provider_id = provider.scope.provider_id
        retained = self.records.get(provider_id)
        contract = retained.contract if retained else self._contract(provider)
        if not order and retained is None:
            return NativeLocationInput(
                provider.schema.empty_frame(), contract, lineage=lineage
            )

        missing = selection.ids.difference(retained.ids) if retained else selection.ids
        held_additions = retained.added_ids if retained else frozenset()
        if self.report is not None:
            self.report.observe_provider(
                use_id, selection.ids - missing - held_additions, executed=False
            )

        if retained is None or missing:
            change = self._changes.get(provider_id)
            added = (
                frozenset(key for key in missing if key in change.additions)
                if change
                else frozenset()
            )
            frames = [retained.frame] if retained else []
            frames.extend(self._resolve_base(selection, missing - added, use_id))
            if change and added:
                additions = decode_additions(
                    [change.additions[key] for key in sorted(added)],
                    provider.schema.metadata,
                )
                self._publish_records(
                    provider, additions, RecordReferences(change.scope, added)
                )
                frames.append(additions)
            frame = (
                pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            )
            retained = NativeLocationInput(
                frame, contract, added_ids=held_additions | added
            )
            self.records[provider_id] = retained

        ordered = (
            retained.frame.set_index("location_id", drop=False)
            .loc[order]
            .reset_index(drop=True)
        )
        return NativeLocationInput(
            ordered, contract, added_ids=retained.added_ids, lineage=lineage
        )

    def _resolve_base(
        self,
        selection: PreparedProviderInput,
        missing: frozenset[str],
        use_id: str | None,
    ) -> list[gpd.GeoDataFrame]:
        frames = []
        if missing and self.provider_records is not None and selection.provider.cache:
            cached = self.provider_records.lookup(selection.provider, sorted(missing))
            if not cached.empty:
                frames.append(cached)
                missing = missing.difference(cached.location_id)
                if self.report is not None:
                    self.report.observe_provider(
                        use_id, cached.location_id, executed=False
                    )
        if missing:
            acquired = acquire_provider_input(
                selection, self.scope, needed_ids=sorted(missing)
            )
            self._publish_records(selection.provider, acquired.frame)
            frames.append(acquired.frame)
            if self.report is not None:
                self.report.observe_provider(use_id, missing, executed=True)
        return frames

    def _publish_records(
        self,
        provider: RegisteredProvider,
        frame: gpd.GeoDataFrame,
        references: RecordReferences | None = None,
    ) -> None:
        if (
            self.provider_records is None
            or provider.scope.provider_id not in self.retained_providers
        ):
            return
        with persistence_boundary(self.report.job_id) if self.report else nullcontext():
            self.provider_records.publish(provider, frame, references=references)


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
