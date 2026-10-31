import hashlib
import json
import struct

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
from lyra.sdk import ipc_codec
from lyra.sdk.typed_storage import Snapshot

from tests.storage_data import declarations, frame


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_round_trip(state: str, *, spatial: bool) -> None:
    source = frame(state, spatial=spatial).drop(columns="category")
    schema = declarations(spatial=spatial).remove_columns(["category"])
    if spatial:
        source = gpd.GeoDataFrame(source)
        source["id"] = pd.Series([str(i) for i in range(len(source))], dtype=str)
        schema = schema.add_columns({"id": pandera.Column(str)})
        snapshot = Snapshot.from_locations(
            source, schema, collection="places", unit_type="point"
        )
    else:
        snapshot = Snapshot.from_frame(source, schema)
    mutable = bytearray(ipc_codec.encode(snapshot))
    restored = ipc_codec.decode(mutable)
    mutable[:] = b"x" * len(mutable)
    pd.testing.assert_frame_equal(restored.to_frame(), snapshot.to_frame())
    assert restored.scope == snapshot.scope
    assert restored.fingerprints == snapshot.fingerprints
    assert restored.table.schema == snapshot.table.schema
    if snapshot.geometry is not None:
        assert restored.geometry is not None
        assert restored.geometry.equals(snapshot.geometry)


def test_native_category() -> None:
    snapshot = Snapshot.from_frame(frame("populated"), declarations())
    pd.testing.assert_frame_equal(
        ipc_codec.decode(ipc_codec.encode(snapshot)).to_frame(), snapshot.to_frame()
    )


@pytest.mark.parametrize("change", ["truncate", "payload", "version"])
def test_corruption(change: str) -> None:
    payload = bytearray(ipc_codec.encode(Snapshot.from_frame(frame(), declarations())))
    if change == "truncate":
        del payload[-20:]
    elif change == "payload":
        payload[-20] ^= 1
    else:
        payload[7] = 99
    with pytest.raises(ValueError, match="typed IPC artifact"):
        ipc_codec.decode(payload)


@pytest.mark.parametrize(
    "field", ["protocol", "content_protocol", "fingerprints", "scope"]
)
def test_invalid_metadata(field: str) -> None:
    payload = ipc_codec.encode(Snapshot.from_frame(frame(), declarations()))
    body = payload[40:]
    length = struct.unpack("<Q", body[:8])[0]
    metadata = json.loads(body[8 : 8 + length])
    metadata[field] = 999
    encoded = json.dumps(metadata).encode()
    body = struct.pack("<Q", len(encoded)) + encoded + body[8 + length :]
    with pytest.raises(ValueError, match="typed IPC artifact"):
        ipc_codec.decode(payload[:8] + hashlib.sha256(body).digest() + body)


def test_malformed_type_metadata_and_nullability() -> None:
    original = Snapshot.from_frame(frame(), declarations())
    for table in (
        original.table.replace_schema_metadata({b"pandas": b"invalid json"}),
        pa.Table.from_arrays(
            [pa.array([None], type=pa.int64())],
            schema=pa.schema([pa.field("value", pa.int64(), nullable=False)]),
        ),
    ):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        encoded = sink.getvalue().to_pybytes()
        metadata = json.dumps(
            {
                "protocol": 2,
                "content_protocol": 2,
                "scope": None,
                "fingerprints": original.fingerprints,
                "table_length": len(encoded),
            }
        ).encode()
        body = struct.pack("<Q", len(metadata)) + metadata + encoded
        with pytest.raises(ValueError, match="typed IPC artifact"):
            ipc_codec.decode(b"LYRAIPC\x02" + hashlib.sha256(body).digest() + body)


def test_inconsistent_reconstructed_column_names() -> None:
    original = Snapshot.from_frame(frame(), declarations())
    metadata = dict(original.table.schema.metadata or {})
    pandas_metadata = json.loads(metadata[b"pandas"])
    pandas_metadata["columns"][0]["name"] = "different_name"
    metadata[b"pandas"] = json.dumps(pandas_metadata).encode()
    inconsistent = Snapshot(original.table.replace_schema_metadata(metadata))
    # Fingerprints cover typed contents, so reconstruction metadata must also
    # be checked even when the envelope and content digests are valid.
    assert inconsistent.fingerprints == original.fingerprints
    with pytest.raises(ValueError, match="reconstruction column names"):
        ipc_codec.decode(ipc_codec.encode(inconsistent))
