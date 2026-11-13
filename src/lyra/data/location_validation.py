"""Private integrity checks for internal location snapshots."""

from dataclasses import replace

import pyarrow as pa
import pyarrow.compute as pc

from lyra.data.location_scope import validate_scope
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.typed_storage import Snapshot


def validate_snapshot(snapshot: LocationSnapshot) -> LocationSnapshot:
    """Validate intrinsic contents and detach externally writable buffers.

    Returns:
        A checked snapshot with immutable buffers.

    Raises:
        ValueError: If the native location contents are invalid.
    """
    scope = replace(snapshot.scope)
    if scope.metric:
        message = "Location snapshots require a location scope"
        raise ValueError(message)
    validate_scope(scope.collection, scope.unit_type, scope.crs)
    if scope.contract is not None:
        type(scope.contract).model_validate(scope.contract.model_dump())
    table, geometry = snapshot.table, snapshot.geometry
    owned = getattr(snapshot, "_owned_buffers", None)
    if owned is None or owned[0] is not table or owned[1] is not geometry:
        # Read-only external buffers can still alias writable producer memory.
        # Only storage constructors and internal owned derivations establish ownership.
        snapshot = LocationSnapshot(table, geometry, scope)
        table, geometry = snapshot.table, snapshot.geometry
    # Owned geometry has already passed the WKB reader and geometry checks.
    Snapshot.validate_contents(table, geometry, scope, geometry_validated=True)
    for field, source in zip(table.schema, table.columns, strict=True):
        column = source
        dtype = field.type
        if pa.types.is_dictionary(dtype):
            dtype = dtype.value_type
            column = pc.cast(column, dtype)
        if field.name == scope.id_column:
            if (
                not (pa.types.is_string(dtype) or pa.types.is_large_string(dtype))
                or pc.call_function(
                    "any", [pc.call_function("equal", [column, pa.scalar("")])]
                ).as_py()
            ):
                message = "Location identifiers must be nonempty strings"
                raise ValueError(message)
        elif not any(
            check(dtype)
            for check in (
                pa.types.is_null,
                pa.types.is_boolean,
                pa.types.is_integer,
                pa.types.is_floating,
                pa.types.is_string,
                pa.types.is_large_string,
                pa.types.is_date,
                pa.types.is_timestamp,
            )
        ):
            message = f"Unsupported location property type: {dtype}"
            raise ValueError(message)
        if (
            pa.types.is_floating(dtype)
            and pc.call_function("any", [pc.call_function("is_inf", [column])]).as_py()
        ):
            message = "Location properties must not contain infinity"
            raise ValueError(message)
    return snapshot
