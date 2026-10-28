"""Validation and normalization of spatial metric inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lyra.sdk.models.spatial import BoundsReference, LocationReference
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from lyra.sdk.models.plugin import SpatialInputKind

_LOCATION_WRAPPER_ADAPTER = TypeAdapter(LocationReference)
_BOUNDS_WRAPPER_ADAPTER = TypeAdapter(BoundsReference)


class SpatialInputValidationError(Exception):
    """Report API-compatible validation failures for spatial input fields."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Initialize the error with structured field-level validation details."""
        self.errors = errors
        super().__init__("spatial input validation failed")


class SpatialInputResolutionUnavailableError(Exception):
    """Indicate that an external spatial lookup could not be completed."""


def _adapter_for_kind(kind: SpatialInputKind) -> TypeAdapter[Any]:
    return _LOCATION_WRAPPER_ADAPTER if kind == "location" else _BOUNDS_WRAPPER_ADAPTER


def _format_pydantic_errors(
    field_name: str,
    exc: PydanticValidationError,
) -> list[dict[str, Any]]:
    return [
        {
            "loc": [field_name, *list(error.get("loc", []))],
            "msg": str(error.get("msg", "Invalid spatial input.")),
            "type": str(error.get("type", "value_error")),
        }
        for error in exc.errors()
    ]


def resolve_spatial_inputs(
    payload: dict[str, Any],
    spatial_inputs: dict[str, SpatialInputKind],
    *,
    converter_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate and replace declared spatial wrappers with resolved GeoJSON.

    Returns:
        A copy of the payload containing serialized GeoJSON for spatial fields.

    Raises:
        SpatialInputValidationError: If a wrapper or converted geometry is invalid.
        SpatialInputResolutionUnavailableError: If an external lookup fails.
    """
    resolved = dict(payload)
    for field_name, kind in spatial_inputs.items():
        try:
            wrapper = _adapter_for_kind(kind).validate_python(payload[field_name])
        except PydanticValidationError as exc:
            raise SpatialInputValidationError(
                _format_pydantic_errors(field_name, exc)
            ) from exc

        data_type = str(wrapper.data_type)
        value = wrapper.value
        converter = converter_map[kind][data_type]

        try:
            geojson = converter(value)
        except PydanticValidationError as exc:
            raise SpatialInputValidationError(
                _format_pydantic_errors(field_name, exc)
            ) from exc
        except ValueError as exc:
            raise SpatialInputValidationError(
                [{"loc": [field_name], "msg": str(exc), "type": "value_error"}]
            ) from exc
        except (KeyError, SQLAlchemyError) as exc:
            msg = f"Failed to resolve spatial input {field_name!r}."
            raise SpatialInputResolutionUnavailableError(msg) from exc

        resolved[field_name] = geojson.model_dump(mode="json")

    return resolved
