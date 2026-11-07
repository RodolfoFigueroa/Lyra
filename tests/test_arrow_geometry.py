import pyarrow as pa
import pytest
import shapely

from lyra.data.geometry_buffers import SCHEMA, decode, encode
from test_support.location_data import assert_exact, invalid_inputs, payload


def test_matrix_chunks_slices() -> None:
    raw = [f["geometry"] for f in payload()["features"]]
    table = encode(raw)
    for candidate in (table, pa.concat_tables([table.slice(0, 4), table.slice(4)])):
        assert_exact([g.model_dump() for g in decode(candidate)], raw)
        assert_exact([g.model_dump() for g in decode(candidate.slice(2, 5))], raw[2:7])
    assert decode(encode([])) == []


@pytest.mark.parametrize("name", ["nonfinite", "open_ring"])
def test_invalid_geometry(name: str) -> None:
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        encode(f["geometry"] for f in invalid_inputs()[name]["features"])


def test_schema() -> None:
    with pytest.raises(ValueError, match=r"Invalid|validation error"):
        decode(pa.table({"type": [0]}))


@pytest.mark.parametrize("value", [None, b"invalid", b"\x01\x01"])
def test_invalid_wkb(value: bytes | None) -> None:

    with pytest.raises((ValueError, shapely.errors.GEOSException)):
        decode(
            pa.Table.from_arrays([pa.array([value], type=pa.binary())], schema=SCHEMA)
        )
