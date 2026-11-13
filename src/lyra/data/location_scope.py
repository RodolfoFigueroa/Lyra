"""Validation of declared location scope independent of record contents."""

import pyproj
from pydantic import TypeAdapter

from lyra.contracts.locations import CollectionName, UnitType
from lyra.contracts.strict import StrictBaseModel


class _LocationScope(StrictBaseModel):
    collection: CollectionName
    unit_type: UnitType


_CRS = TypeAdapter(str)


def validate_scope(collection: str, unit_type: str, crs: str) -> None:
    """Check scope names and require a recognized CRS without transforming data."""
    _LocationScope(collection=collection, unit_type=unit_type)
    pyproj.CRS.from_user_input(_CRS.validate_python(crs, strict=True))
