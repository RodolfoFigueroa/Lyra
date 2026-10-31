import struct

import pyarrow as pa
import pytest
from lyra.sdk.geometry_buffers import SCHEMA, decode, encode

from tests.location_data import assert_exact, invalid_inputs, payload


def test_matrix_chunks_slices() -> None:
    raw = [f["geometry"] for f in payload()["features"]]
    raw.append(
        {"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0, 5.0, 6.0, 7.0]]}
    )
    table = encode(raw)
    for candidate in (table, pa.concat_tables([table.slice(0, 4), table.slice(4)])):
        assert_exact([g.model_dump() for g in decode(candidate)], raw)
        assert_exact([g.model_dump() for g in decode(candidate.slice(2, 5))], raw[2:7])
    assert decode(encode([])) == []


@pytest.mark.parametrize("name", ["nonfinite", "open_ring"])
def test_invalid_geometry(name: str) -> None:
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        encode(f["geometry"] for f in invalid_inputs()[name]["features"])


@pytest.mark.parametrize(
    ("tag", "coordinates"),
    [
        (None, []),
        (6, []),
        (-1, []),
        (0, []),
        (0, [[[[1.0]]]]),
        (0, [[[[1.0, float("nan")]]]]),
        (0, [[[[None, 2.0]]]]),
        (2, [[[[1.0, 2.0]]]]),
        (4, [[]]),
        (5, [None]),
        (3, [[[[1.0, 2.0]]]]),
        (0, None),
    ],
)
def test_invalid_buffers(tag: int | None, coordinates: object) -> None:
    table = pa.Table.from_pylist(
        [{"type": tag, "coordinates": coordinates}], schema=SCHEMA
    )
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        decode(table)


def test_schema() -> None:
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        decode(pa.table({"type": [0]}))


def test_writable_buffer_and_independent_decode() -> None:
    buffer = bytearray(struct.pack("dd", 1.0, 2.0))
    array = pa.Array.from_buffers(pa.float64(), 2, [None, pa.py_buffer(buffer)])
    for _ in range(4):
        array = pa.LargeListArray.from_arrays(
            pa.array([0, len(array)], type=pa.int64()), array
        )
    table = pa.Table.from_arrays([pa.array([0], type=pa.int8()), array], schema=SCHEMA)
    first = decode(table)
    struct.pack_into("d", buffer, 0, 9.0)
    assert first[0].coordinates == [1.0, 2.0]
    assert decode(table)[0].coordinates == [9.0, 2.0]


def test_malformed_offsets() -> None:
    valid = encode([{"type": "MultiPoint", "coordinates": []}])
    column = valid.column("coordinates").combine_chunks()
    offsets = bytearray(struct.pack("qq", 0, 1))
    array = pa.Array.from_buffers(
        column.type, 1, [None, pa.py_buffer(offsets)], children=[column.values]
    )
    table = pa.Table.from_arrays([valid.column("type"), array], schema=SCHEMA)
    struct.pack_into("q", offsets, 8, -1)
    with pytest.raises(pa.ArrowInvalid):
        decode(table)
