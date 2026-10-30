"""Shared synchronous resolution of named, scoped ID selections."""

from collections import Counter
from collections.abc import Mapping
from typing import Annotated

from lyra.sdk.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    UnresolvedLocationIDsError,
)
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider, SelectedLocationProvider
from lyra.sdk.models.location import Locations, LocationSelection, validated_locations
from lyra.sdk.resources import (
    BaseIO,
    ResourceConfig,
    ResourceConfigurationError,
    ResourceScope,
)
from pydantic import Field, ValidationError


class _LocationLookup(LocationSelection):
    """Internal selection allowing an empty set of IDs."""

    ids: list[Annotated[str, Field(min_length=1)]]


def _validate_selections(
    selections: Mapping[str, LocationSelection],
    selected: SelectedLocationProvider,
    *,
    allow_empty: bool = False,
) -> dict[str, LocationSelection]:
    collections = {item.name: item for item in selected.declaration.collections}
    validated = {}
    for name, raw_selection in selections.items():
        try:
            # Revalidate mutable models and bypassed constructors.
            model = _LocationLookup if allow_empty else LocationSelection
            selection = model.model_validate(raw_selection.model_dump())
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
    schema: LocationOutputSchema,
) -> Locations:
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
    return _validate_output(schema, name, selection, crs, output)


def _validate_output(
    schema: LocationOutputSchema,
    name: str,
    selection: LocationSelection,
    crs: str,
    output: object,
) -> Locations:
    """Validate scope, schema and exact identity before restoring request order.

    Returns:
        Normalized locations in request order.

    Raises:
        LocationProviderContractError: If the provider violates its declaration.
    """
    if not isinstance(output, Locations):
        message = "Provider must return Locations"
        raise LocationProviderContractError(name, message, selection.ids)
    try:
        counts = Counter(output.ids)
        duplicate = [unit_id for unit_id, count in counts.items() if count > 1]
        if duplicate:
            raise LocationProviderContractError(
                name, "Provider IDs must be unique", duplicate
            )
        location = validated_locations(output)
    except (ValidationError, TypeError, ValueError, AttributeError):
        message = "Provider returned a malformed feature collection"
        raise LocationProviderContractError(name, message, selection.ids) from None
    if (location.collection, location.unit_type) != (
        selection.collection,
        selection.unit_type,
    ):
        raise LocationProviderContractError(
            name, "Provider scope differs from requested scope", selection.ids
        )
    try:
        location = schema.validate(location)
    except Exception as exc:
        raise LocationProviderContractError(
            name, "Provider output failed its location schema", selection.ids
        ) from exc
    counts = Counter(location.ids)
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
    if location.ids == selection.ids:
        return location
    features = {feature.id: feature for feature in location.features}
    return Locations.model_validate(
        {
            **location.model_dump(),
            "features": [features[unit_id] for unit_id in selection.ids],
        }
    )


def _get_provider(scope: ResourceScope, resource: str) -> LocationProvider:
    provider = scope.get(resource)
    if not isinstance(provider, LocationProvider):
        message = "Selected resource must be a LocationProvider"
        raise ResourceConfigurationError(message)
    return provider


class LocationResolver:
    """Resolve through a caller-owned resource scope without activating or closing it.

    The owner activates the scope and closes it after all lookups and execution.
    The registered provider class supplies schemas even when acquisition uses an
    override. Construction and empty lookups do not acquire resources.
    """

    def __init__(
        self,
        scope: ResourceScope,
        *,
        selected: SelectedLocationProvider | None,
        provider: type[LocationProvider] | None,
    ) -> None:
        """Bind the execution scope and registered provider declaration."""
        self.scope = scope
        self.selected = selected
        self.provider = provider

    def resolve_locations(
        self,
        collection: str,
        unit_type: str,
        ids: list[str],
        *,
        input_name: str = "locations",
        node: str | None = None,
    ) -> Locations:
        """Resolve exact scoped IDs, including empty internal selections.

        Returns:
            Validated locations in first-occurrence ID order.

        """
        try:
            selection = _LocationLookup(
                collection=collection, unit_type=unit_type, ids=ids
            )
        except ValidationError:
            error = LocationSelectionError(
                input_name,
                "Invalid location selection",
                ids if isinstance(ids, list) else [],
            )
            error.node = node
            if node is not None:
                error.add_note(f"Receiving node: {node!r}")
            raise error from None
        return self.resolve({input_name: selection}, node=node, allow_empty=True)[
            input_name
        ]

    def resolve(
        self,
        selections: Mapping[str, LocationSelection],
        *,
        node: str | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Locations]:
        """Validate and resolve named inputs using the shared provider contract.

        Returns:
            Independent validated values under receiving input names.

        Raises:
            ResourceConfigurationError: If no provider declaration is configured.
            LocationSelectionError: If an input is invalid.
            UnresolvedLocationIDsError: If requested IDs are unknown.
            LocationProviderContractError: If output violates its declaration.
            LocationAcquisitionError: If provider setup or resolution fails.
        """
        if not selections:
            return {}
        if self.selected is None or self.provider is None:
            message = "Location resolution requires a configured location provider"
            error = ResourceConfigurationError(message)
            error.input_name = next(iter(selections))
            error.add_note(f"Receiving location inputs: {tuple(selections)!r}")
            if node is not None:
                error.add_note(f"Receiving node: {node!r}")
            raise error
        try:
            validated = _validate_selections(
                selections, self.selected, allow_empty=allow_empty
            )
            collections = {
                item.name: item for item in self.selected.declaration.collections
            }
            return {
                name: self._resolve_input(
                    name, selection, collections[selection.collection].crs
                )
                for name, selection in validated.items()
            }
        except (
            LocationSelectionError,
            UnresolvedLocationIDsError,
            LocationProviderContractError,
            LocationAcquisitionError,
        ) as exc:
            exc.node = node
            if node is not None:
                exc.add_note(f"Receiving node: {node!r}")
            raise
        except ResourceConfigurationError as exc:
            if node is not None:
                exc.add_note(f"Receiving node: {node!r}")
            raise

    def _resolve_input(
        self, name: str, selection: LocationSelection, crs: str
    ) -> Locations:
        if self.provider is None or self.selected is None:
            message = "Location resolution requires a configured location provider"
            raise ResourceConfigurationError(message)
        schema = self.provider.outputs[selection.collection, selection.unit_type]
        if not selection.ids:
            try:
                empty = Locations.model_validate(
                    {
                        "collection": selection.collection,
                        "unit_type": selection.unit_type,
                        "crs": {"type": "name", "properties": {"name": crs}},
                        "features": [],
                    }
                )
            except ValidationError:
                message = "Provider declaration has invalid location metadata"
                raise LocationProviderContractError(name, message) from None
            return _validate_output(schema, name, selection, crs, empty)
        try:
            provider = _get_provider(self.scope, self.selected.resource)
        except ResourceConfigurationError as exc:
            exc.input_name = name
            exc.add_note(f"Receiving location input: {name!r}")
            raise
        except Exception as exc:
            message = f"Location provider setup failed for input {name!r}"
            raise LocationAcquisitionError(
                message,
                collection=selection.collection,
                unit_type=selection.unit_type,
                ids=selection.ids,
                input_name=name,
            ) from exc
        return _resolve_input(provider, name, selection, crs, schema)


def resolve_locations(
    selections: Mapping[str, LocationSelection],
    *,
    selected: SelectedLocationProvider | None,
    resources: Mapping[str, type[BaseIO]],
    configuration: Mapping[str, ResourceConfig] | None = None,
    overrides: Mapping[str, type[BaseIO]] | None = None,
) -> dict[str, Locations]:
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
    declaration = resources.get(selected.resource)
    if declaration is None or not issubclass(declaration, LocationProvider):
        message = "Selected resource must declare a LocationProvider"
        raise ResourceConfigurationError(message)
    scope = ResourceScope(resources, configuration, overrides)
    resolver = LocationResolver(scope, selected=selected, provider=declaration)
    try:
        with scope.activate():
            result = resolver.resolve(selections)
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
