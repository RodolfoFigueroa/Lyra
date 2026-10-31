"""Standalone artifacts retain exact content and reject untrusted payloads."""

import hashlib
import json
import struct
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pytest
from lyra.sdk import location_artifacts as codec
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import assert_exact, columns, variants


@pytest.mark.parametrize("raw", variants().values())
def test_roundtrip(raw: dict, tmp_path: Path) -> None:
    snapshot = LocationSnapshot(*columns(raw))
    path = tmp_path / "location.arrow"
    path.write_bytes(codec.encode(snapshot))
    source = bytearray(path.read_bytes())
    restored = codec.decode(memoryview(source))
    source[:] = bytes(len(source))
    assert_exact(restored.to_dict(), raw)
    for field in ("fingerprint", "geometry_fingerprint", "non_geometry_fingerprint"):
        assert getattr(restored, field) == getattr(snapshot, field)
    for table in (restored.geometry, restored.table):
        for column in table.columns:
            for chunk in column.chunks:
                assert all(b is None or not b.is_mutable for b in chunk.buffers())


@pytest.mark.parametrize(
    "damage",
    ["truncated", "version", "metadata", "digest", "payload", "structure", "ids"],
)
def test_corruption(damage: str) -> None:
    encoded = codec.encode(LocationSnapshot(*columns(variants()["base"])))
    if damage == "truncated":
        encoded = encoded[:-20]
    else:

        def change(metadata: dict, table: pa.Table, geometry: bytes) -> tuple:
            if damage == "version":
                metadata["protocol"] = 99
            elif damage == "metadata":
                metadata.pop("scope")
            elif damage == "digest":
                metadata["fingerprints"][2] = "0" * 64
            elif damage in {"payload", "ids"}:
                table = table.set_column(
                    0,
                    table.schema.field(0),
                    pa.array(
                        ["duplicate"] * table.num_rows
                        if damage == "ids"
                        else [f"new{i}" for i in range(table.num_rows)]
                    ),
                )
            else:
                metadata["scope"]["columns"] = ["bad"]
            return metadata, table, geometry

        encoded = alter(encoded, change)
    with pytest.raises(codec.ArtifactDecodeError, match=r".+"):
        codec.decode(encoded)


def alter(payload: bytes, change: Callable) -> bytes:
    body = payload[40:]
    length = struct.unpack("<Q", body[:8])[0]
    metadata = json.loads(body[8 : 8 + length])
    data = body[8 + length :]
    table = pa.ipc.open_stream(data[: metadata["table_length"]]).read_all()
    metadata, table, geometry = change(
        metadata, table, data[metadata["table_length"] :]
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    encoded = sink.getvalue().to_pybytes()
    metadata["table_length"] = len(encoded)
    header = json.dumps(metadata).encode()
    body = struct.pack("<Q", len(header)) + header + encoded + geometry
    return payload[:8] + hashlib.sha256(body).digest() + body


def test_old_format_is_rejected() -> None:
    table = pa.table({"id": ["a"]}).replace_schema_metadata(
        {b"artifact": b'{"version":1,"representation":"lyra-location-columns"}'}
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    with pytest.raises(
        codec.ArtifactDecodeError, match="Unsupported artifact protocol"
    ):
        codec.decode(sink.getvalue().to_pybytes())
