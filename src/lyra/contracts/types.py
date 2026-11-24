"""Shared type aliases for plugin and metric implementations."""

from __future__ import annotations

from typing import TypeAlias

from pydantic import ConfigDict, JsonValue, TypeAdapter

JsonScalar: TypeAlias = bool | int | float | str | None
JsonObject: TypeAlias = dict[str, JsonValue]

_FINITE_JSON = ConfigDict(strict=True, allow_inf_nan=False)
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue, config=_FINITE_JSON)
_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject, config=_FINITE_JSON)


def validate_json_value(value: object) -> JsonValue:
    """Validate and normalize an arbitrary boundary value as recursive JSON.

    Returns:
        The validated JSON-compatible value.
    """
    return _JSON_VALUE_ADAPTER.validate_python(value)


def validate_json_object(value: object) -> JsonObject:
    """Validate and normalize an arbitrary boundary value as a JSON object.

    Returns:
        The validated JSON object.
    """
    return _JSON_OBJECT_ADAPTER.validate_python(value)
