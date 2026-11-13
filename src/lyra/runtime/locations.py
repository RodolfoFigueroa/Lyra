"""Shared provider acquisition into existing execution and storage bindings."""

from collections.abc import Mapping
from typing import Any

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.resources import ResourceConfig
from lyra.data.locations import Locations
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.provider_acquisition import acquire_provider_input
from lyra.runtime.resources import ResourceScope
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.resources import BaseIO


class LocationResolver:
    """Borrow a resource scope and retain authoritative captured declarations."""

    def __init__(
        self, scope: ResourceScope, *, providers: Mapping[str, RegisteredProvider]
    ) -> None:
        """Bind metadata without constructing resources."""
        self.scope = scope
        self.providers = providers

    def resolve_prepared(self, prepared: PreparedExecution) -> dict[str, Locations]:
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
            return self.resolve(prepared.request.inputs)
        except (
            LocationAcquisitionError,
            LocationSelectionError,
            LocationProviderContractError,
        ) as exc:
            exc.node = prepared.node
            raise

    def resolve(
        self, selections: Mapping[str, LocationSelection]
    ) -> dict[str, Locations]:
        """Validate all selections before resolving any input.

        Returns:
            Scoped runtime values containing every declared provider attribute.

        Raises:
            LocationSelectionError: If a selection or provider is invalid.
        """
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
            orders[name] = list(dict.fromkeys(selection.ids))
            prepared.append(
                PreparedProviderInput(name, frozenset(selection.ids), registered)
            )
        values = {}
        for selection in prepared:
            acquired = acquire_provider_input(selection, self.scope)
            schema = LocationOutputSchema(
                selection.provider.schema.schema, id_column="location_id"
            )
            values[selection.name] = schema.build_contract(
                acquired.frame, selection.provider.contract
            ).select_ids(orders[selection.name])
        return values


def resolve_locations(
    selections: Mapping[str, LocationSelection],
    *,
    providers: Mapping[str, RegisteredProvider],
    resources: Mapping[str, type[BaseIO[Any]]],
    configuration: Mapping[str, ResourceConfig] | None = None,
    overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
) -> dict[str, Locations]:
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
