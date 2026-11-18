"""Single-collection providers using the ordinary resource lifecycle."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

import geopandas as gpd

from lyra.contracts.origins import Origin
from lyra.sdk.output_schema import OutputSchema
from lyra.sdk.resources import BaseIO, ConfigT


class LocationProvider(BaseIO[ConfigT], ABC):
    """Expose fixed, schema-validated records under one public provider ID.

    Register the class as a resource. Its registration key chooses configuration
    and dependencies; its ID, code revision, and origins identify the records.
    Providers return ordinary GeoDataFrames and the runtime validates them.
    """

    id: ClassVar[str]
    schema: ClassVar[OutputSchema]
    cache: ClassVar[bool] = False
    code_revision: ClassVar[str | None] = None
    description: ClassVar[str | None] = None
    origins: ClassVar[Sequence[Origin]] = ()

    @abstractmethod
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return exactly one record for each requested nonempty, deduplicated ID."""

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return requested IDs present in the complete base dataset.

        This optional capability reads membership without fetching records.
        Missing IDs are omitted; resource and query failures must propagate.
        Selections and interventions do not affect base membership.

        Raises:
            NotImplementedError: If the provider does not implement membership.
        """
        raise NotImplementedError
