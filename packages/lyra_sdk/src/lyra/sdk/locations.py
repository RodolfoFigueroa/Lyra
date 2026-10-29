"""Managed batch location providers and service-free collection declarations."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, ClassVar, Self

from lyra.sdk.errors import PluginDefinitionError
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.models.location import CollectionName, Locations, UnitType
from lyra.sdk.models.output import LocationOutputMetadata
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.resources import BaseIO, ConfigT, ResourceConfigurationError
from pydantic import ConfigDict, Field, model_validator


class LocationCollection(StrictBaseModel):
    """Describe a collection without acquiring clients or reading credentials.

    Unit types are independent provider-defined categories. The CRS identifier
    labels every geometry returned for this collection; providers supply it in
    the CRS object on the returned Locations value.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    name: CollectionName
    unit_types: tuple[UnitType, ...] = Field(min_length=1)
    crs: Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
    outputs: dict[str, LocationOutputMetadata] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_unit_types(self) -> Self:
        """Reject repeated unit types.

        Returns:
            The validated declaration.

        Raises:
            ValueError: If unit types repeat.
        """
        if len(set(self.unit_types)) != len(self.unit_types):
            message = "Collection unit types must be unique"
            raise ValueError(message)
        return self


class LocationProvider(BaseIO[ConfigT], ABC):
    """Resolve authoritative locations using the existing resource lifecycle.

    Declare collections on the class so inspection needs no instance, clients,
    or credentials. Configure acquisition through config_model, secret_names,
    setup, and close inherited from BaseIO. Wrap acquisition operations in
    self.operation(...) to use resource logging, including outside a queued job.

    Providers own ID interpretation, geometries, properties, and CRS. Within a
    collection and unit type, an ID must always identify the same geometry.
    Returned inputs are read-only to consumers; copy features before editing.
    """

    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {}

    collections: ClassVar[tuple[LocationCollection, ...]] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate collection declarations without constructing a provider.

        Raises:
            PluginDefinitionError: If declarations are empty, malformed, or repeat.
        """
        super().__init_subclass__(**kwargs)
        if (
            not isinstance(cls.collections, tuple)
            or not cls.collections
            or any(not isinstance(item, LocationCollection) for item in cls.collections)
        ):
            message = "Providers require a nonempty tuple of LocationCollection values"
            raise PluginDefinitionError(message)
        names = [item.name for item in cls.collections]
        if len(set(names)) != len(names):
            message = "Provider collection names must be unique"
            raise PluginDefinitionError(message)

        scopes = {
            (item.name, unit) for item in cls.collections for unit in item.unit_types
        }
        if (
            not isinstance(cls.outputs, dict)
            or set(cls.outputs) != scopes
            or any(
                not isinstance(output, LocationOutputSchema)
                for output in cls.outputs.values()
            )
        ):
            message = (
                "Provider outputs require a LocationOutputSchema "
                "for every declared scope"
            )
            raise PluginDefinitionError(message)

    @abstractmethod
    def resolve(self, collection: str, unit_type: str, ids: list[str]) -> Locations:
        """Synchronously resolve exactly the requested IDs in one scope.

        The shared resolver supplies a supported collection and unit type and a
        nonempty, deduplicated ID list. Return each requested ID exactly once,
        preserving its spelling, with authoritative geometry and properties
        (which may be empty). Do not expand selections or return partial results.

        Returns:
            Features with the collection's declared CRS.

        Raises:
            UnresolvedLocationIDsError: With every unresolved ID, including when none
                resolve. Wrong-type IDs are unresolved within the requested scope.
            LocationAcquisitionError: If acquisition fails. Use exception chaining
                for the underlying cause and avoid credentials in the message.
        """


class LocationProviderManifest(StrictBaseModel):
    """Service-free provider declaration referencing a registered resource."""

    resource: str = Field(min_length=1, pattern=r"^\S+$")
    collections: tuple[LocationCollection, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_collections(self) -> Self:
        """Return metadata with unique collection names.

        Raises:
            ValueError: If collection names repeat.
        """
        names = [collection.name for collection in self.collections]
        if len(names) != len(set(names)):
            message = "Provider collection names must be unique"
            raise ValueError(message)
        return self


@dataclass(frozen=True)
class SelectedLocationProvider:
    """The sole enabled plugin's provider and named resource configuration key."""

    distribution: str
    declaration: LocationProviderManifest

    @property
    def resource(self) -> str:
        """Resource key used with the existing ResourceScope configuration."""
        return self.declaration.resource


def select_location_provider(
    declarations: Mapping[str, LocationProviderManifest | None],
    *,
    required: bool = False,
) -> SelectedLocationProvider | None:
    """Select the sole declaration among enabled plugins without acquisition.

    Deployment plugin enablement and LocalRunner plugin selection determine the
    candidates. Multiple declarations are ambiguous even if resources match.
    Settings and credentials remain under the selected resource's existing key.

    Returns:
        The selected provider, or None when absent and not required.

    Raises:
        ResourceConfigurationError: If providers are ambiguous or required but absent.
    """
    candidates = [
        SelectedLocationProvider(distribution, declaration)
        for distribution, declaration in sorted(declarations.items())
        if declaration is not None
    ]
    if len(candidates) > 1:
        names = ", ".join(candidate.distribution for candidate in candidates)
        message = f"Multiple active location providers: {names}"
        raise ResourceConfigurationError(message)
    if not candidates:
        if required:
            message = "Location resolution requires a configured location provider"
            raise ResourceConfigurationError(message)
        return None
    return candidates[0]
