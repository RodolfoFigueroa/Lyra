"""Standalone artifacts retain exact content and reject untrusted payloads."""

import json
from pathlib import Path

import pyarrow as pa
import pytest
from lyra.sdk import location_artifacts as codec
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import assert_exact, variants
from tests.test_location_snapshot import columns


@pytest.mark.parametrize("raw", variants().values())
@pytest.mark.parametrize(("batch", "level"), [(1, 1), (3, 3), (65536, 9)])
def test_roundtrip(
    raw: dict, batch: int, level: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codec, "_BATCH_SIZE", batch)
    monkeypatch.setattr(codec, "_COMPRESSION_LEVEL", level)
    snapshot = LocationSnapshot(*columns(raw))
    path = tmp_path / "location.arrow"
    path.write_bytes(codec.encode(snapshot))
    source = bytearray(path.read_bytes())
    restored = codec.decode(memoryview(source))
    source[:] = bytes(len(source))
    assert_exact(restored.to_dict(), raw)
    for field in ("fingerprint", "geometry_fingerprint", "non_geometry_fingerprint"):
        assert getattr(restored, field) == getattr(snapshot, field)
    for table in (restored.geometry, restored.properties):
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
        table = pa.ipc.open_file(encoded).read_all()
        metadata = dict(table.schema.metadata)
        stored = json.loads(metadata[b"artifact"])
        if damage == "version":
            stored["version"] = 99
        elif damage == "metadata":
            metadata.pop(b"location")
        elif damage == "digest":
            stored["fingerprint"] = "0" * 64
        elif damage in {"payload", "ids"}:
            ids = (
                ["duplicate"] * table.num_rows
                if damage == "ids"
                else [f"new{i}" for i in range(table.num_rows)]
            )
            table = table.set_column(
                2, table.schema.field(2), pa.array(ids, type=pa.large_string())
            )
        else:
            table = table.set_column(
                0,
                table.schema.field(0),
                pa.array([99] * table.num_rows, type=pa.int8()),
            )
        metadata[b"artifact"] = json.dumps(stored).encode()
        table = table.replace_schema_metadata(metadata)
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        encoded = sink.getvalue().to_pybytes()
    with pytest.raises(codec.ArtifactDecodeError, match="Cannot decode"):
        codec.decode(encoded)
