"""Internal columnar identity and scalar codec.

Each property is a struct with a tag and separate typed payloads. Tags distinguish
absence from null; integer magnitudes use hexadecimal strings without a width limit.
Property names live in schema metadata so they cannot collide with the ID column.
Arrow inputs may alias producer memory; snapshot ownership is a separate concern.
"""

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

import pyarrow as pa
from lyra.sdk.models.geometry import CRS
from lyra.sdk.models.location_payload import LocationPayload

Scalar = str | bool | int | float | None
_VALUE = pa.struct(
    [
        ("tag", pa.int8()),
        ("string", pa.large_string()),
        ("boolean", pa.bool_()),
        ("integer", pa.large_string()),
        ("float", pa.float64()),
    ]
)
_TYPES = (type(None), str, bool, int, float)


def _cell(value: Scalar, *, absent: bool = False) -> dict[str, Any]:
    if type(value) not in _TYPES or (type(value) is float and not math.isfinite(value)):
        message = "Properties must be finite JSON scalars"
        raise ValueError(message)
    tag = 0 if absent else _TYPES.index(type(value)) + 1
    payload: list[Any] = [None] * 4
    if tag >= 2:
        payload[tag - 2] = hex(value) if type(value) is int else value
    return dict(
        zip(
            ("tag", "string", "boolean", "integer", "float"),
            [tag, *payload],
            strict=True,
        )
    )


def encode(
    ids: Iterable[str],
    properties: Iterable[Mapping[str, Scalar]],
    *,
    collection: str,
    unit_type: str,
    crs: CRS | dict[str, Any],
) -> pa.Table:
    """Validate and encode ordered IDs, sparse properties, and exact metadata.

    Returns:
        An Arrow table supporting ordinary column access, slicing, and chunking.

    Raises:
        ValueError: For invalid identities, scalar values, or metadata.
    """
    scope = LocationPayload(
        collection=collection,
        unit_type=unit_type,
        crs=CRS.model_validate(crs),
        features=[],
    )
    identifiers = list(ids)
    rows = list(properties)
    if any(type(value) is not str or not value for value in identifiers):
        message = "IDs must be nonempty strings"
        raise ValueError(message)
    if len(set(identifiers)) != len(identifiers):
        message = "IDs must be unique within the scope"
        raise ValueError(message)
    if len(rows) != len(identifiers):
        message = "Property and ID counts must match"
        raise ValueError(message)
    if any(type(key) is not str for row in rows for key in row):
        message = "Property keys must be strings"
        raise ValueError(message)
    keys = list(dict.fromkeys(key for row in rows for key in row))
    arrays = [pa.array(identifiers, type=pa.large_string())]
    arrays.extend(
        pa.array(
            [_cell(row.get(key), absent=key not in row) for row in rows], type=_VALUE
        )
        for key in keys
    )
    metadata = scope.model_dump(exclude={"features"})
    metadata["keys"] = keys
    return pa.Table.from_arrays(
        arrays, names=["id", *[f"property_{i}" for i in range(len(keys))]]
    ).replace_schema_metadata(
        {b"location": json.dumps(metadata, ensure_ascii=True).encode()}
    )


def decode(table: pa.Table) -> dict[str, Any]:
    """Validate buffers and return independently mutable identity/property data.

    Returns:
        Scope, CRS, ordered IDs, and sparse property dictionaries.

    Raises:
        ValueError: For malformed schema, metadata, or scalar payloads.
    """
    table.validate(full=True)
    try:
        return _decode(table)
    except (KeyError, TypeError, OverflowError, AttributeError) as exc:
        message = "Invalid property buffers"
        raise ValueError(message) from exc


def _decode(table: pa.Table) -> dict[str, Any]:
    metadata = json.loads((table.schema.metadata or {})[b"location"])
    keys = metadata.pop("keys")
    if (
        not isinstance(keys, list)
        or any(type(key) is not str for key in keys)
        or len(set(keys)) != len(keys)
    ):
        message = "Invalid property keys"
        raise ValueError(message)
    expected = pa.schema(
        [
            ("id", pa.large_string()),
            *[(f"property_{i}", _VALUE) for i in range(len(keys))],
        ]
    )
    if table.schema.remove_metadata() != expected:
        message = "Invalid property buffer schema"
        raise ValueError(message)
    rows: list[dict[str, Scalar]] = [{} for _ in range(table.num_rows)]
    for index, key in enumerate(keys, 1):
        for row, cell in zip(rows, table.column(index).to_pylist(), strict=True):
            if cell is None or cell["tag"] not in range(6):
                message = "Invalid scalar tag"
                raise ValueError(message)
            tag = cell["tag"]
            value = (
                None
                if tag < 2
                else cell[("string", "boolean", "integer", "float")[tag - 2]]
            )
            if tag == 4:
                value = int(str(value), 16)
            if cell != _cell(value, absent=tag == 0):
                message = "Invalid scalar payload"
                raise ValueError(message)
            if tag:
                row[key] = value
    ids = table.column(0).to_pylist()
    encode(ids, rows, **metadata)
    return {**metadata, "ids": ids, "properties": rows}
