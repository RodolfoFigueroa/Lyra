"""SHA-256 content protocol 1 for validated location columns.

Fields are unsigned little-endian 64-bit length followed by bytes. Arrays encode
length, validity bytes, and typed payloads recursively; offsets start at zero.
Numeric payloads use little-endian bytes and null payloads are zero. Strings use
UTF-8, normalized int64 offsets, and concatenated bytes. Property columns are
sorted by name; metadata uses sorted compact JSON (metadata only). Domains and
protocol version separate geometry, properties, and the combined digest.
"""

import hashlib
import json
import struct
from typing import Protocol

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc


class _Hasher(Protocol):
    def update(self, value: bytes | memoryview, /) -> None: ...


def _field(hasher: _Hasher, value: bytes | memoryview) -> None:
    hasher.update(struct.pack("<Q", len(value)))
    hasher.update(value)


def _array(hasher: _Hasher, array: pa.Array) -> None:
    _field(hasher, str(array.type).encode())
    _field(hasher, struct.pack("<Q", len(array)))
    _field(hasher, array.is_valid().to_numpy(zero_copy_only=False).tobytes())
    if pa.types.is_struct(array.type):
        for child in array.flatten():
            _array(hasher, child)
    elif pa.types.is_large_list(array.type):
        offsets = array.offsets.to_numpy()
        start, end = int(offsets[0]), int(offsets[-1])
        _field(hasher, (offsets - start).astype("<i8").tobytes())
        _array(hasher, array.values.slice(start, end - start))
    elif pa.types.is_large_string(array.type):
        # Null slots may span arbitrary bytes in valid Arrow arrays. Only their
        # validity is logical content; normalize their physical payloads.
        array = pc.fill_null(array, "")
        buffers = array.buffers()
        offsets = np.frombuffer(buffers[1], dtype=np.int64)[
            array.offset : array.offset + len(array) + 1
        ]
        start, end = int(offsets[0]), int(offsets[-1])
        _field(hasher, (offsets - start).astype("<i8").tobytes())
        _field(hasher, memoryview(buffers[2])[start:end])
    else:
        if pa.types.is_boolean(array.type):
            array = pc.cast(array, pa.uint8())
        values = pc.fill_null(array, 0).to_numpy(zero_copy_only=False)
        _field(hasher, values.astype(values.dtype.newbyteorder("<")).tobytes())


def compute(geometry: pa.Table, properties: pa.Table) -> tuple[str, str, str]:
    """Return geometry, non-geometry, and combined hex digests of valid columns."""
    geo = hashlib.sha256(b"lyra-content\x00\x01geometry")
    for column in geometry.columns:
        _array(geo, column.combine_chunks())
    other = hashlib.sha256(b"lyra-content\x00\x01properties")
    metadata = json.loads(properties.schema.metadata[b"location"])
    keys = metadata.pop("keys")
    _field(other, json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    _array(other, properties.column(0).combine_chunks())
    for key, index in sorted((key, i) for i, key in enumerate(keys, 1)):
        cells = properties.column(index).combine_chunks()
        if not np.any(cells.field("tag").to_numpy()):
            continue
        _field(other, key.encode())
        _array(other, cells)
    combined = hashlib.sha256(b"lyra-content\x00\x01snapshot")
    _field(combined, geo.digest())
    _field(combined, other.digest())
    return geo.hexdigest(), other.hexdigest(), combined.hexdigest()
