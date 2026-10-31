"""Exact typed content fingerprints over owned Arrow contents."""

import hashlib
import struct
from typing import Protocol

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from lyra.sdk.geometry_buffers import SCHEMA as GEOMETRY_SCHEMA

_VALID_BLOCK = b"\x01" * (64 * 1024)


class _Hasher(Protocol):
    def update(self, value: bytes | memoryview, /) -> None: ...


def _field(hasher: _Hasher, value: bytes | memoryview) -> None:
    hasher.update(struct.pack("<Q", len(value)))
    hasher.update(value)


def _typed_array(hasher: _Hasher, array: pa.Array) -> None:
    """Hash canonical native buffers, retaining exact temporal and float bits."""
    _field(hasher, str(array.type).encode())
    _field(hasher, struct.pack("<Q", len(array)))
    if pa.types.is_dictionary(array.type):
        _typed_array(hasher, array.dictionary)
        _typed_array(hasher, array.indices)
        return
    if _property_buffers(hasher, array):
        return
    # Native reconstruction removes slice offsets, unused null payloads and
    # unreachable list children. Arrow scalars retain nanosecond precision.
    canonical = pa.array(
        [scalar if scalar.is_valid else None for scalar in array], type=array.type
    )
    _field(hasher, canonical.is_valid().to_numpy(zero_copy_only=False).tobytes())
    if pa.types.is_struct(canonical.type):
        for child in canonical.flatten():
            _typed_array(hasher, child)
    elif pa.types.is_list(canonical.type) or pa.types.is_large_list(canonical.type):
        _typed_array(hasher, canonical.offsets)
        _typed_array(hasher, canonical.values)
    else:
        for buffer in canonical.buffers()[1:]:
            _field(hasher, b"" if buffer is None else memoryview(buffer))


def _validity(hasher: _Hasher, array: pa.Array) -> np.ndarray | None:
    """Emit protocol validity.

    Returns:
        A validity mask when nulls exist, otherwise None.
    """
    if not array.null_count:
        _nonnull_validity(hasher, len(array))
        return None
    valid = array.is_valid().to_numpy(zero_copy_only=False)
    _field(hasher, memoryview(valid).cast("B"))
    return valid


def _property_buffers(hasher: _Hasher, array: pa.Array) -> bool:
    """Emit canonical scalar buffers.

    Returns:
        Whether the type has an optimized buffer implementation.
    """
    dtype = array.type
    variable = (
        pa.types.is_string(dtype)
        or pa.types.is_large_string(dtype)
        or pa.types.is_binary(dtype)
        or pa.types.is_large_binary(dtype)
    )
    fixed = (
        pa.types.is_integer(dtype)
        or pa.types.is_floating(dtype)
        or pa.types.is_date(dtype)
        or pa.types.is_time(dtype)
        or pa.types.is_timestamp(dtype)
        or pa.types.is_duration(dtype)
        or pa.types.is_fixed_size_binary(dtype)
    )
    if not (variable or fixed or pa.types.is_boolean(dtype) or pa.types.is_null(dtype)):
        return False
    valid = _validity(hasher, array)
    if pa.types.is_null(dtype):
        return True
    if variable:
        _variable_buffers(hasher, array, valid)
        return True
    buffer = array.buffers()[1]
    if pa.types.is_boolean(dtype):
        if buffer is None:
            _field(hasher, b"")
            return True
        start = array.offset // 8
        shift = array.offset % 8
        packed = np.frombuffer(buffer, dtype=np.uint8)[
            start : (array.offset + len(array) + 7) // 8
        ]
        bits = np.unpackbits(packed, bitorder="little")[shift : shift + len(array)]
        if valid is not None:
            bits &= valid
        _field(hasher, np.packbits(bits, bitorder="little").data)
        return True
    width = (
        dtype.byte_width
        if pa.types.is_fixed_size_binary(dtype)
        else dtype.bit_width // 8
    )
    start = array.offset * width
    values = (
        b""
        if buffer is None
        else memoryview(buffer)[start : start + len(array) * width]
    )
    if valid is not None:
        normalized_values = (
            np.frombuffer(values, dtype=np.uint8).copy().reshape(len(array), width)
        )
        normalized_values[~valid] = 0
        values = normalized_values.data.cast("B")
    _field(hasher, values)
    return True


def _variable_buffers(
    hasher: _Hasher, array: pa.Array, valid: np.ndarray | None
) -> None:
    """Hash compact variable-width offsets and referenced data."""
    dtype = array.type
    empty = "" if pa.types.is_string(dtype) or pa.types.is_large_string(dtype) else b""
    canonical = (
        pc.fill_null(array, pa.scalar(empty, type=dtype))
        if valid is not None
        else array
    )
    _, offsets_buffer, data = canonical.buffers()
    large = pa.types.is_large_string(dtype) or pa.types.is_large_binary(dtype)
    width = 8 if large else 4
    offsets = np.frombuffer(
        offsets_buffer,
        dtype="<i8" if large else "<i4",
        count=len(array) + 1,
        offset=canonical.offset * width,
    )
    start, end = int(offsets[0]), int(offsets[-1])
    normalized = offsets - start if start else offsets
    _field(hasher, normalized.data.cast("B"))
    _field(hasher, b"" if data is None else memoryview(data)[start:end])


def _nonnull_geometry(array: pa.Array) -> bool:
    """Check only referenced children for buffer-hashing eligibility.

    Returns:
        Whether every referenced geometry value is nonnull.
    """
    while True:
        if array.null_count:
            return False
        if not pa.types.is_large_list(array.type):
            return True
        offsets = array.offsets.to_numpy()
        start, end = int(offsets[0]), int(offsets[-1])
        array = array.values.slice(start, end - start)


def _nonnull_header(hasher: _Hasher, data_type: pa.DataType, length: int) -> None:
    _field(hasher, str(data_type).encode())
    _field(hasher, struct.pack("<Q", length))
    _nonnull_validity(hasher, length)


def _nonnull_validity(hasher: _Hasher, length: int) -> None:
    hasher.update(struct.pack("<Q", length))
    while length:
        count = min(length, len(_VALID_BLOCK))
        hasher.update(memoryview(_VALID_BLOCK)[:count])
        length -= count


def _geometry_array(hasher: _Hasher, array: pa.Array) -> None:
    """Hash nonnull geometry using referenced buffers and normalized offsets."""
    _nonnull_header(hasher, array.type, len(array))
    if pa.types.is_large_list(array.type):
        offsets = array.offsets.to_numpy()
        start, end = int(offsets[0]), int(offsets[-1])
        normalized = offsets - start if start else offsets
        _nonnull_header(hasher, pa.int64(), len(offsets))
        _field(hasher, normalized.data.cast("B"))
        _geometry_array(hasher, array.values.slice(start, end - start))
        return
    buffer = array.buffers()[1]
    width = array.type.bit_width // 8
    start = array.offset * width
    _field(
        hasher,
        b""
        if buffer is None
        else memoryview(buffer)[start : start + len(array) * width],
    )


def typed_table(table: pa.Table) -> str:
    """Return protocol 2 identity of typed contents, excluding all metadata.

    Length-prefixed fields encode row count, ordered names and native types,
    validity and canonical Arrow buffers. Native dictionary domains and ordering
    remain part of categorical identity. Schema nullability is a validation rule,
    not data identity. Chunking and allocation layout are never hashed.
    """
    digest = hashlib.sha256(b"lyra-content\x00\x02table")
    _field(digest, struct.pack("<Q", table.num_rows))
    geometry = table.schema.equals(GEOMETRY_SCHEMA, check_metadata=False)
    for name, column in zip(table.column_names, table.columns, strict=True):
        _field(digest, name.encode())
        array = column.chunk(0) if column.num_chunks == 1 else column.combine_chunks()
        if geometry and _nonnull_geometry(array):
            _geometry_array(digest, array)
        else:
            _typed_array(digest, array)
    return digest.hexdigest()


def typed_snapshot(
    table: pa.Table, geometry: pa.Table | None, scope: tuple[str, ...] | None
) -> tuple[str, str, str]:
    """Return protocol 2 geometry, scoped properties and combined identities."""
    geo = hashlib.sha256(b"lyra-content\x00\x02geometry")
    if geometry is not None:
        _field(geo, typed_table(geometry).encode())
    other = hashlib.sha256(b"lyra-content\x00\x02properties")
    _field(other, typed_table(table).encode())
    for value in scope or ():
        _field(other, value.encode())
    combined = hashlib.sha256(b"lyra-content\x00\x02snapshot")
    _field(combined, geo.digest())
    _field(combined, other.digest())
    return geo.hexdigest(), other.hexdigest(), combined.hexdigest()
