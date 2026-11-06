"""Managed batch location providers and service-free collection declarations."""

from abc import ABC, abstractmethod
from typing import ClassVar

import geopandas as gpd

from lyra.contracts import providers
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.locations import LocationContract
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.resources import BaseIO, ConfigT


class LocationProvider(BaseIO[ConfigT], ABC):
    """Resolve authoritative locations using the existing resource lifecycle.

    Declare collections and concrete contracts on the class so inspection needs no
    instance, clients,
    or credentials. Configure acquisition through config_model, secret_names,
    setup, and close inherited from BaseIO. Wrap acquisition operations in
    self.operation(...) to use resource logging, including outside a queued job.

    Providers own ID interpretation, geometries, properties, and CRS. IDs identify
    features within a collection and unit type; their contents may change.
    Return a GeoDataFrame with native columns. The resolver attaches requested scope and
    validates and orders the frame before constructing an immutable snapshot.
    """

    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {}

    contracts: ClassVar[tuple[LocationContract, ...]] = ()

    collections: ClassVar[tuple[providers.LocationCollection, ...]] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate collection declarations without constructing a provider.

        Raises:
            PluginDefinitionError: If declarations are empty, malformed, or repeat.
        """
        super().__init_subclass__(**kwargs)
        if (
            not isinstance(cls.collections, tuple)
            or not cls.collections
            or any(
                not isinstance(item, providers.LocationCollection)
                for item in cls.collections
            )
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

        if not isinstance(cls.contracts, tuple) or any(
            not isinstance(item, LocationContract) for item in cls.contracts
        ):
            message = "Providers require a tuple of LocationContract values"
            raise PluginDefinitionError(message)
        try:
            providers.LocationProviderManifest(
                resource="provider",
                collections=cls.collections,
                contracts=cls.contracts,
            )
        except ValueError as exc:
            raise PluginDefinitionError(str(exc)) from exc
        for contract in cls.contracts:
            family = contract.family
            if (
                cls.outputs[family.collection, family.unit_type].metadata
                != family.structure
            ):
                message = "Provider contract differs from its executable schema"
                raise PluginDefinitionError(message)

    @abstractmethod
    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        """Synchronously resolve exactly the requested IDs in one scope.

        The shared resolver supplies a registered concrete contract and a
        nonempty, deduplicated ID list. Return each requested ID exactly once,
        preserving its spelling, with authoritative geometry and properties
        (which may be empty). Do not expand selections or return partial results.

        The contract carries source metadata and its declared revision. Providers
        must access that observation; metadata alone creates no storage snapshot.

        Returns:
            An unvalidated frame result with the collection's declared CRS.

        Raises:
            UnresolvedLocationIDsError: With every unresolved ID, including when none
                resolve. Wrong-type IDs are unresolved within the requested scope.
            LocationAcquisitionError: If acquisition fails. Use exception chaining
                for the underlying cause and avoid credentials in the message.
        """
