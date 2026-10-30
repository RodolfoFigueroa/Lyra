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

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
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


def _metadata(table: pa.Table) -> dict[str, Any]:
    metadata = json.loads((table.schema.metadata or {})[b"location"])
    if set(metadata) != {"keys", "collection", "unit_type", "crs"}:
        message = "Invalid property metadata fields"
        raise ValueError(message)
    keys = metadata["keys"]
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
    LocationPayload.model_validate(
        {k: v for k, v in metadata.items() if k != "keys"} | {"features": []}
    )
    return metadata


def _validate_cells(cells: pa.StructArray) -> None:
    tags = cells.field("tag")
    if cells.null_count or tags.null_count:
        message = "Invalid scalar tag"
        raise ValueError(message)
    codes = tags.to_numpy()
    if np.any((codes < 0) | (codes > 5)):
        message = "Invalid scalar tag"
        raise ValueError(message)
    for tag, name in enumerate(("string", "boolean", "integer", "float"), 2):
        column = cells.field(name)
        active = codes == tag
        if not np.array_equal(column.is_valid().to_numpy(zero_copy_only=False), active):
            message = "Invalid scalar payload"
            raise ValueError(message)
        selected = column.filter(pa.array(active))
        if tag == 4:
            for value in selected.to_pylist():
                if hex(int(value, 16)) != value:
                    message = "Invalid integer payload"
                    raise ValueError(message)
        elif tag == 5 and not np.all(np.isfinite(selected.to_numpy())):
            message = "Invalid nonfinite scalar payload"
            raise ValueError(message)


def validate(table: pa.Table) -> None:
    """Validate identity and scalar buffers without decoding or re-encoding rows.

    Raises:
        ValueError: If metadata, identities, or scalar payloads are invalid.
    """
    table.validate(full=True)
    try:
        _validate_table(table)
    except (KeyError, TypeError, OverflowError, AttributeError) as exc:
        message = "Invalid property buffers"
        raise ValueError(message) from exc


def _validate_table(table: pa.Table) -> None:
    _metadata(table)
    ids = table.column(0)
    if (
        ids.null_count
        or pc.call_function(
            "any",
            [pc.call_function("equal", [pc.call_function("utf8_length", [ids]), 0])],
        ).as_py()
    ):
        message = "IDs must be nonempty strings"
        raise ValueError(message)
    if pc.call_function("count_distinct", [ids]).as_py() != table.num_rows:
        message = "IDs must be unique within the scope"
        raise ValueError(message)
    for batch in table.to_batches(max_chunksize=4096):
        for cells in batch.columns[1:]:
            _validate_cells(cells)


def property_data(table: pa.Table) -> dict[str, Any]:
    """Materialize detached scalar data from already validated columns.

    Returns:
        Metadata, IDs, and independent sparse property dictionaries.
    """
    metadata = json.loads(table.schema.metadata[b"location"])
    keys = metadata.pop("keys")
    rows = []
    for batch in table.to_batches(max_chunksize=4096):
        group: list[dict[str, Scalar]] = [{} for _ in range(batch.num_rows)]
        for key, cells in zip(keys, batch.columns[1:], strict=True):
            tags = cells.field("tag").to_numpy()
            for tag, name in enumerate(("string", "boolean", "integer", "float"), 2):
                indices = np.flatnonzero(tags == tag)
                values = cells.field(name).take(indices).to_pylist()
                for index, value in zip(indices, values, strict=True):
                    group[index][key] = int(value, 16) if tag == 4 else value
            for index in np.flatnonzero(tags == 1):
                group[index][key] = None
        rows.extend(group)
    return {**metadata, "ids": table.column(0).to_pylist(), "properties": rows}


def decode(table: pa.Table) -> dict[str, Any]:
    """Validate arbitrary buffers and return independently mutable scalar data.

    Returns:
        Metadata, IDs, and independent sparse property dictionaries.
    """
    validate(table)
    return property_data(table)
