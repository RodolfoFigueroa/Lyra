"""Public native intervention values with independent frame ownership."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from lyra.contracts.provider_scope import InterventionID

if TYPE_CHECKING:
    import geopandas as gpd

_ID = TypeAdapter(InterventionID)
_REMOVALS = TypeAdapter(list[InterventionID])


@dataclass(frozen=True, kw_only=True, eq=False)
class Intervention:
    """One set identity and independently captured native actions for a catalog.

    Provider-dependent validation occurs when the value is accepted against a
    registered schema. Acceptance captures another independent snapshot.
    """

    id: str
    catalog: str
    revision: str
    add: gpd.GeoDataFrame | None = field(default=None, repr=False)
    remove: list[str] | None = None

    def __post_init__(self) -> None:
        """Capture actions without consulting a registration or acquiring resources.

        Raises:
            TypeError: If additions are not a GeoDataFrame.
            ValueError: If the set is empty or repeats removal IDs.
        """
        if self.add is not None and not isinstance(
            self.add, import_module("geopandas").GeoDataFrame
        ):
            msg = "Intervention additions require a GeoDataFrame"
            raise TypeError(msg)
        removals = _REMOVALS.validate_python(
            [] if self.remove is None else self.remove, strict=True
        )
        if len(removals) != len(set(removals)):
            msg = "Repeated removal IDs are not allowed"
            raise ValueError(msg)
        if (self.add is None or self.add.empty) and not removals:
            msg = "An intervention must contain at least one addition or removal"
            raise ValueError(msg)
        for name in ("id", "catalog", "revision"):
            object.__setattr__(self, name, _ID.validate_python(getattr(self, name)))
        if self.add is not None:
            detached = self.add.copy(deep=True)
            detached.attrs = deepcopy(self.add.attrs)
            object.__setattr__(self, "add", detached)
        object.__setattr__(self, "remove", removals)
