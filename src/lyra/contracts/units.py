"""Column unit validation shared by schema and retained metadata."""

from __future__ import annotations

from functools import cache
from importlib import import_module
from threading import Lock
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, StrictStr

if TYPE_CHECKING:
    from pint import UnitRegistry


_REGISTRY_LOCK = Lock()


@cache
def _registry() -> UnitRegistry:
    # Import Pint only when validating a non-null unit.
    return import_module("pint").UnitRegistry()


def _normalize(value: str) -> str:
    if not value.strip():
        message = "A unit expression must contain more than whitespace"
        raise ValueError(message)
    # Concurrent first declarations must share one registry.
    with _REGISTRY_LOCK:
        registry = _registry()
    try:
        return str(registry.parse_units(value))
    except Exception as error:
        # Pint's expression parser can also raise Python token and arithmetic errors.
        message = f"Invalid unit expression: {value!r}"
        raise ValueError(message) from error


ColumnUnit = Annotated[StrictStr, AfterValidator(_normalize)] | None
