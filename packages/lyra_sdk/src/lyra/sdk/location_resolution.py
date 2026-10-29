"""Shared synchronous resolution of named, scoped ID selections."""

from collections import Counter
from collections.abc import Mapping

from lyra.sdk.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    UnresolvedLocationIDsError,
)
from lyra.sdk.locations import LocationProvider, SelectedLocationProvider
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.location import LocationSelection, ResolvedLocation
from lyra.sdk.resources import (
    BaseIO,
    ResourceConfig,
    ResourceConfigurationError,
    ResourceScope,
)
from pydantic import ValidationError


def _validate_selections(
    selections: Mapping[str, LocationSelection], selected: SelectedLocationProvider
) -> dict[str, LocationSelection]:
    collections = {item.name: item for item in selected.declaration.collections}
    validated = {}
    for name, raw_selection in selections.items():
        try:
            # Revalidate mutable models and bypassed constructors.
            selection = LocationSelection.model_validate(raw_selection.model_dump())
        except (ValidationError, AttributeError, TypeError):
            message = "Invalid location selection"
            ids = (
                raw_selection.ids
                if isinstance(raw_selection, LocationSelection)
                and isinstance(raw_selection.ids, list)
                else []
            )
            raise LocationSelectionError(name, message, ids) from None
        ids = list(dict.fromkeys(selection.ids))
        collection = collections.get(selection.collection)
        if collection is None or selection.unit_type not in collection.unit_types:
            message = "Undeclared collection or unit type"
            raise LocationSelectionError(name, message, ids)
        validated[name] = selection.model_copy(update={"ids": ids})
    return validated


def _resolve_input(
    provider: LocationProvider,
    name: str,
    selection: LocationSelection,
    crs: str,
) -> ResolvedLocation:
    try:
        output = provider.resolve(
            selection.collection, selection.unit_type, list(selection.ids)
        )
    except UnresolvedLocationIDsError as exc:
        if (
            exc.collection != selection.collection
            or exc.unit_type != selection.unit_type
            or not exc.ids
            or len(set(exc.ids)) != len(exc.ids)
            or not set(exc.ids).issubset(selection.ids)
        ):
            message = "Provider reported invalid unresolved IDs or scope"
            raise LocationProviderContractError(name, message, exc.ids) from None
        raise UnresolvedLocationIDsError(
            selection.collection, selection.unit_type, exc.ids, input_name=name
        ) from None
    except ValidationError:
        message = "Provider returned a malformed feature collection"
        raise LocationProviderContractError(name, message, selection.ids) from None
    except Exception as exc:
        message = f"Location acquisition failed for input {name!r}"
        raise LocationAcquisitionError(
            message,
            collection=selection.collection,
            unit_type=selection.unit_type,
            ids=selection.ids,
            input_name=name,
        ) from exc
    if not isinstance(output, GeoJSON):
        message = "Provider must return LocationInput"
        raise LocationProviderContractError(name, message, selection.ids)
    try:
        location = GeoJSON.model_validate(output.model_dump(), strict=True)
    except (ValidationError, TypeError, ValueError, AttributeError):
        message = "Provider returned a malformed feature collection"
        raise LocationProviderContractError(name, message, selection.ids) from None
    counts = Counter(feature.id for feature in location.features)
    requested = set(selection.ids)
    missing = [unit_id for unit_id in selection.ids if unit_id not in counts]
    extra = [unit_id for unit_id in counts if unit_id not in requested]
    duplicate = [unit_id for unit_id, count in counts.items() if count > 1]
    if missing or extra or duplicate:
        message = "Provider IDs must match requested IDs exactly"
        raise LocationProviderContractError(
            name, message, list(dict.fromkeys([*missing, *extra, *duplicate]))
        )
    if location.crs.properties.name != crs:
        message = "Provider CRS differs from the collection declaration"
        raise LocationProviderContractError(name, message, selection.ids)
    features = {feature.id: feature for feature in location.features}
    location.features = [features[unit_id] for unit_id in selection.ids]
    return ResolvedLocation(
        collection=selection.collection,
        unit_type=selection.unit_type,
        location=location,
    )


def _get_provider(scope: ResourceScope, resource: str) -> LocationProvider:
    provider = scope.get(resource)
    if not isinstance(provider, LocationProvider):
        message = "Selected resource must be a LocationProvider"
        raise ResourceConfigurationError(message)
    return provider


def resolve_locations(
    selections: Mapping[str, LocationSelection],
    *,
    selected: SelectedLocationProvider | None,
    resources: Mapping[str, type[BaseIO]],
    configuration: Mapping[str, ResourceConfig] | None = None,
    overrides: Mapping[str, type[BaseIO]] | None = None,
) -> dict[str, ResolvedLocation]:
    """Validate all inputs, resolve exact IDs, and release resources before returning.

    The selected declaration supplies collection metadata. Resource configuration
    and subclass overrides use the same keys and rules as graph execution. No
    resources are acquired for an empty input mapping. Acquisition diagnostics
    omit underlying exception text, which may contain credentials.

    Returns:
        Independent scoped inputs in caller order, with first-occurrence ID order.

    Raises:
        LocationSelectionError: If an input is empty, malformed, or undeclared.
        UnresolvedLocationIDsError: If any requested ID cannot be resolved.
        LocationProviderContractError: If provider output violates its contract.
        LocationAcquisitionError: If setup, resolution, or cleanup fails.
        ResourceConfigurationError: If provider selection or configuration is invalid.
    """
    if not selections:
        return {}
    if selected is None:
        message = "Location resolution requires a configured location provider"
        raise ResourceConfigurationError(message)
    validated = _validate_selections(selections, selected)
    scope = ResourceScope(resources, configuration, overrides)
    collections = {item.name: item for item in selected.declaration.collections}
    try:
        with scope.activate():
            provider = _get_provider(scope, selected.resource)
            result = {
                name: _resolve_input(
                    provider, name, selection, collections[selection.collection].crs
                )
                for name, selection in validated.items()
            }
    except (
        LocationSelectionError,
        UnresolvedLocationIDsError,
        LocationProviderContractError,
        LocationAcquisitionError,
        ResourceConfigurationError,
    ):
        raise
    except Exception as exc:
        message = "Location provider setup or cleanup failed"
        raise LocationAcquisitionError(message) from exc
    return result
