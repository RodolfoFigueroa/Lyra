"""Snapshot ownership and cross-column validation without external resources."""

import struct

import numpy as np
import pyarrow as pa
import pytest
from lyra.sdk import geometry_buffers, property_buffers
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import assert_exact, variants


def columns(raw: dict) -> tuple[pa.Table, pa.Table]:
    features = raw["features"]
    return (
        geometry_buffers.encode(f["geometry"] for f in features),
        property_buffers.encode(
            (f["id"] for f in features),
            (f["properties"] for f in features),
            **{k: v for k, v in raw.items() if k != "features"},
        ),
    )


@pytest.mark.parametrize("raw", variants().values())
def test_round_trip_and_reconstruction(raw: dict) -> None:
    geometry, properties = columns(raw)
    snapshot = LocationSnapshot(geometry, properties)
    assert_exact(snapshot.to_dict(), raw)
    count = geometry.num_rows
    indices = pa.array(list(reversed(range(count))), type=pa.int64())
    rebuilt = LocationSnapshot(
        snapshot.geometry.take(indices), snapshot.properties.take(indices)
    )
    expected = {**raw, "features": list(reversed(raw["features"]))}
    assert_exact(rebuilt.to_dict(), expected)
    assert_exact(snapshot.to_dict(), raw)
    if count:
        raw["features"][0]["geometry"]["coordinates"][0] = 100
        raw["crs"]["properties"]["name"] = "EPSG:3857"
        export = snapshot.to_dict()
        other = snapshot.to_dict()
        export["features"][0]["geometry"]["coordinates"].clear()
        export["features"][0]["properties"]["new"] = 4
        export["features"][0]["id"] = "changed"
        export["crs"]["properties"]["name"] = "EPSG:3857"
        assert_exact(snapshot.to_dict(), other)


def test_writable_arrow_and_numpy_slices() -> None:
    raw = variants()["base"]
    geometry, properties = columns(raw)
    coordinates = geometry.column("coordinates").chunk(0)
    values = coordinates
    for _ in range(4):
        values = values.values
    backing = values.buffers()[1]
    assert backing.is_mutable
    tags = np.zeros(geometry.num_rows, dtype=np.int8)
    # Property tags exercise a NumPy producer as well as sliced Arrow buffers.
    cells = properties.column(1).chunk(0)
    tags[:] = cells.field("tag").to_numpy()
    cells = pa.StructArray.from_arrays(
        [pa.array(tags), *[cells.field(i) for i in range(1, 5)]],
        fields=list(cells.type),
    )
    properties = properties.set_column(1, properties.schema.field(1), cells)
    snapshot = LocationSnapshot(geometry.slice(1, 3), properties.slice(1, 3))
    expected = snapshot.to_dict()
    struct.pack_into("d", memoryview(backing), 16, 999.0)
    tags[:] = 0
    assert_exact(snapshot.to_dict(), expected)
    for table in (snapshot.geometry, snapshot.properties):
        for column in table.columns:
            for chunk in column.chunks:
                for buffer in chunk.buffers():
                    if buffer is not None and buffer.size:
                        assert not buffer.is_mutable
                        assert memoryview(buffer).readonly


def test_reject_invalid_components() -> None:
    geometry, properties = columns(variants()["base"])
    with pytest.raises(ValueError, match="counts"):
        LocationSnapshot(geometry.slice(1), properties)
    with pytest.raises(ValueError, match="schema"):
        LocationSnapshot(geometry.drop(["type"]), properties)
    with pytest.raises(ValueError, match="Invalid"):
        LocationSnapshot(geometry, properties.replace_schema_metadata({}))


def test_model_producers_and_chunked_inputs() -> None:
    raw = variants()["base"]
    geometry, properties = columns(raw)
    models = geometry_buffers.decode(geometry)
    geometry = geometry_buffers.encode(models)
    snapshot = LocationSnapshot(
        pa.concat_tables([geometry.slice(0, 4), geometry.slice(4)]),
        pa.concat_tables([properties.slice(0, 2), properties.slice(2)]),
    )
    models[0].coordinates.clear()
    assert_exact(snapshot.to_dict(), raw)
    # Invalid model instances must still pass through codec validation.
    with pytest.raises(ValueError, match=r".+"):
        geometry_buffers.encode(models)


def test_invalid_values_are_revalidated() -> None:
    geometry, properties = columns(variants()["base"])
    invalid_ids = properties.set_column(
        0,
        properties.schema.field(0),
        pa.array(["duplicate"] * properties.num_rows).cast(pa.large_string()),
    )
    with pytest.raises(ValueError, match="unique"):
        LocationSnapshot(geometry, invalid_ids)
    invalid_tags = geometry.set_column(
        0, geometry.schema.field(0), pa.array([99] * geometry.num_rows, type=pa.int8())
    )
    with pytest.raises(ValueError, match="tag"):
        LocationSnapshot(invalid_tags, properties)
