"""Validation and normalization of spatial metric inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lyra.sdk.models.spatial import BoundsReference, LocationReference
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.converters import bounds, location
from lyra_app.db.connection import DatabaseNotConfiguredError

if TYPE_CHECKING:
    from lyra.sdk.models.plugin import SpatialInputKind
    from sqlalchemy.engine import Engine

    from lyra_app.db.connection import ApplicationDatabaseRuntime

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


async def resolve_spatial_inputs(
    payload: dict[str, Any],
    spatial_inputs: dict[str, SpatialInputKind],
    *,
    database: ApplicationDatabaseRuntime | None,
) -> dict[str, Any]:
    """Validate wrappers and resolve only database selectors in the executor.

    Returns:
        A copy of the payload with serialized geometries.

    Raises:
        SpatialInputValidationError: If an input geometry or selector is invalid.
        DatabaseNotConfiguredError: If a selector requires omitted PostgreSQL.
    """
    resolved = dict(payload)
    pending = []
    for field_name, kind in spatial_inputs.items():
        try:
            wrapper = _adapter_for_kind(kind).validate_python(payload[field_name])
        except PydanticValidationError as exc:
            raise SpatialInputValidationError(
                _format_pydantic_errors(field_name, exc)
            ) from exc
        if wrapper.data_type == "geojson":
            resolved[field_name] = wrapper.value.model_dump(mode="json")
        else:
            pending.append((field_name, kind, wrapper))
    if pending:
        if database is None:
            raise DatabaseNotConfiguredError
        resolved.update(
            await database.run_spatial(
                _resolve_selectors, pending, database.require_spatial_engine()
            )
        )
    return resolved


def _resolve_selectors(
    pending: list[tuple[str, SpatialInputKind, Any]],
    engine: Engine,
) -> dict[str, Any]:
    resolved = {}
    for field_name, kind, wrapper in pending:
        module = location if kind == "location" else bounds
        converter = (
            module.load_from_cvegeos
            if wrapper.data_type == "cvegeo_list"
            else module.load_from_met_zone_code
        )
        try:
            geojson = converter(wrapper.value, engine=engine)
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
