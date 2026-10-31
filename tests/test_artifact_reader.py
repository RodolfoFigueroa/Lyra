"""Public standalone inspection preserves content and limits materialization."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pyarrow as pa
import pytest
from lyra.sdk import (
    ArtifactDecodeError,
    geometry_buffers,
    plugin,
    property_buffers,
    read_location_artifact,
    resources,
)
from lyra.sdk.location_artifacts import encode
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import location_snapshot

from benchmarks.location_baseline.fixtures import assert_exact, variants
from tests.test_location_frames import convert, source_frame
from tests.test_location_snapshot import columns


@pytest.mark.parametrize("raw", variants().values())
def test_inspection(raw: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin, "PluginDefinition", Mock(side_effect=AssertionError("plugin loaded"))
    )
    monkeypatch.setattr(
        resources,
        "ResourceScope",
        Mock(side_effect=AssertionError("resource acquired")),
    )
    source = tmp_path / "bundle"
    source.mkdir()
    original = source / "locations.arrow"
    encoded = encode(LocationSnapshot(*columns(raw)))
    original.write_bytes(encoded)
    path = tmp_path / "detached.arrow"
    path.write_bytes(original.read_bytes())
    original.unlink()
    artifact = read_location_artifact(path)
    assert artifact.metadata["feature_count"] == len(raw["features"])
    assert artifact.metadata["producer_schema_validated"] is False
    for key in ("collection", "unit_type", "crs"):
        assert_exact(artifact.metadata[key], raw[key])
    assert artifact.metadata["keys"] == sorted(
        {key for feature in raw["features"] for key in feature["properties"]}
    )
    metadata = artifact.metadata
    metadata["crs"]["properties"]["name"] = "changed"
    assert_exact(artifact.metadata["crs"], raw["crs"])
    artifact.metadata.clear()
    assert_exact(artifact.to_locations().to_dict(), raw)
    decode = geometry_buffers.geometry_rows
    counts = []

    def bounded(table: pa.Table) -> Iterator[dict[str, Any]]:
        counts.append(table.num_rows)
        return decode(table)

    monkeypatch.setattr(geometry_buffers, "geometry_rows", bounded)
    decode_properties = property_buffers.property_data
    property_counts = []

    def bounded_properties(table: pa.Table) -> dict[str, Any]:
        property_counts.append(table.num_rows)
        return decode_properties(table)

    monkeypatch.setattr(property_buffers, "property_data", bounded_properties)
    for limit in (0, 1, 3, 20):
        preview = artifact.preview(limit)
        assert_exact(preview, raw["features"][:limit])
        for feature in preview:
            feature["properties"]["value"] = "changed"
            feature["geometry"]["coordinates"].clear()
            feature.clear()
        assert counts[-1] <= limit
        assert property_counts[-1] <= limit
    monkeypatch.setattr(geometry_buffers, "geometry_rows", decode)
    monkeypatch.setattr(property_buffers, "property_data", decode_properties)
    assert_exact(artifact.to_locations().to_dict(), raw)
    assert path.read_bytes() == encoded
    invalid_limits: list[Any] = [-1, True, 1.5]
    for limit in invalid_limits:
        with pytest.raises(ValueError, match="limit"):
            artifact.preview(limit)


@pytest.mark.parametrize("damage", ["corrupt", "version"])
def test_reader_errors(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "bad.arrow"
    data = b"invalid"
    if damage == "version":
        table = pa.ipc.open_file(
            encode(LocationSnapshot(*columns(variants()["base"])))
        ).read_all()
        metadata = dict(table.schema.metadata)
        identity = json.loads(metadata[b"artifact"])
        identity["version"] = 99
        metadata[b"artifact"] = json.dumps(identity).encode()
        sink = pa.BufferOutputStream()
        table = table.replace_schema_metadata(metadata)
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        data = sink.getvalue().to_pybytes()
    path.write_bytes(data)
    with pytest.raises(ArtifactDecodeError, match="Cannot decode"):
        read_location_artifact(path)


def test_frame(tmp_path: Path) -> None:
    locations = convert(source_frame())
    path = tmp_path / "frame.arrow"
    path.write_bytes(encode(location_snapshot(locations)))
    artifact = read_location_artifact(path)
    frame = artifact.to_locations().to_geodataframe()
    frame.iloc[0, 0] = "changed"
    assert artifact.to_locations() == locations
