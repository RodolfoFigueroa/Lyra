from dataclasses import replace

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
from lyra.sdk import geometry_shapely, typed_storage
from shapely.geometry import Point

from tests.storage_data import declarations, frame


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
def test_types_and_subsets(state: str) -> None:
    source = frame(state).drop(columns="category")
    snapshot = typed_storage.Snapshot.from_frame(
        source, declarations().remove_columns(["category"])
    )
    expected = source.reset_index(drop=True)
    pd.testing.assert_frame_equal(snapshot.to_frame(), expected)
    for indices in ([], list(reversed(range(len(source))))):
        subset = snapshot.take(pa.array(indices, type=pa.int64()))
        pd.testing.assert_frame_equal(
            subset.to_frame(), expected.iloc[indices].reset_index(drop=True)
        )
        for column in subset.table.columns:
            for chunk in column.chunks:
                assert all(
                    buffer is None or buffer.size == 0 or not buffer.is_mutable
                    for buffer in chunk.buffers()
                )
    names = list(reversed(source.columns))
    pd.testing.assert_frame_equal(snapshot.project(names).to_frame(), expected[names])
    pd.testing.assert_frame_equal(snapshot.slice(0, 0).to_frame(), expected.iloc[:0])


def test_ownership() -> None:
    values = np.array([1, 2], dtype=np.int64)
    snapshot = typed_storage.Snapshot(pa.table({"value": values}))
    values[:] = 9
    exported = snapshot.to_frame()
    exported.iloc[0, 0] = 8
    assert snapshot.table["value"].to_pylist() == [1, 2]
    buffer = snapshot.table["value"].chunk(0).buffers()[1]
    assert memoryview(buffer).readonly


def test_locations() -> None:
    source = gpd.GeoDataFrame(frame("populated", spatial=True))
    source["id"] = ["a", "b", "c"]
    schema = declarations(spatial=True).add_columns({"id": pandera.Column(str)})
    snapshot = typed_storage.Snapshot.from_locations(
        source,
        schema,
        collection="places",
        unit_type="point",
        id_column="id",
    )
    assert snapshot.scope is not None
    assert snapshot.geometry is not None
    assert snapshot.slice(0, 0).scope == snapshot.scope
    selected = snapshot.take(pa.array([1, 0]))
    assert selected.geometry is not None
    assert selected.geometry.num_rows == 2
    with pytest.raises(ValueError, match="row counts"):
        typed_storage.Snapshot(
            snapshot.table, snapshot.geometry.slice(0, 0), snapshot.scope
        )


def test_subsets_do_not_validate_producer_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = pandera.DataFrameSchema(
        {"id": pandera.Column(str)}, checks=pandera.Check(lambda data: len(data) == 2)
    )
    source = pd.DataFrame({"id": ["a", "b"]})
    snapshot = typed_storage.Snapshot.from_frame(source, schema)
    source.iloc[0, 0] = "changed"

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Subset unexpectedly validated or decoded geometry")

    monkeypatch.setattr(schema, "validate", unexpected)
    monkeypatch.setattr(typed_storage.geometry_buffers, "geometry_rows", unexpected)
    assert snapshot.slice(0, 1).to_frame()["id"].tolist() == ["a"]
    assert snapshot.take(pa.array([], type=pa.int64())).table.num_rows == 0
    assert snapshot.project(["id"]).table.num_rows == 2


def test_invalid_structure() -> None:
    with pytest.raises(ValueError, match="Duplicate columns"):
        typed_storage.Snapshot(
            pa.Table.from_arrays([pa.array([1]), pa.array([2])], names=["a", "a"])
        )
    with pytest.raises(ValueError, match="nonnull integer"):
        typed_storage.Snapshot(pa.table({"a": [1]})).take(
            pa.array([None], type=pa.int64())
        )
    with pytest.raises(ValueError, match="Nulls"):
        typed_storage.Snapshot(
            pa.Table.from_arrays(
                [pa.array([None], type=pa.int64())],
                schema=pa.schema([pa.field("a", pa.int64(), nullable=False)]),
            )
        )


def test_typed_identity_layout_and_ownership() -> None:
    values = np.array([1, 2], dtype=np.int64)
    snapshot = typed_storage.Snapshot(pa.table({"a": values}))
    identity = snapshot.fingerprints
    values[:] = 8
    assert snapshot.fingerprints is identity
    for table in (
        pa.table({"a": [0, 1, 2]}).slice(1),
        pa.table({"a": pa.chunked_array([[1], [2]])}),
        snapshot.table.replace_schema_metadata({b"description": b"prose"}),
    ):
        assert typed_storage.Snapshot(table).fingerprints == identity
    assert snapshot.slice(0, 1).fingerprints != identity


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (pa.array([0.0]), pa.array([-0.0])),
        (pa.array([1, 2]), pa.array([2, 1])),
        (pa.array([None, 1]), pa.array([1, None])),
        (pa.array([], type=pa.int32()), pa.array([], type=pa.int64())),
        (pa.array([None], type=pa.int32()), pa.array([None], type=pa.int64())),
        (
            pa.array([1], type=pa.timestamp("ns")),
            pa.array([1], type=pa.timestamp("us")),
        ),
    ],
)
def test_typed_identity_sensitive(left: pa.Array, right: pa.Array) -> None:
    assert (
        typed_storage.Snapshot(pa.table({"a": left})).fingerprints
        != typed_storage.Snapshot(pa.table({"a": right})).fingerprints
    )


def test_typed_identity_null_payload_and_category() -> None:
    dirty = pa.Array.from_buffers(
        pa.int64(),
        2,
        [pa.py_buffer(b"\x02"), pa.py_buffer(np.array([999, 1], dtype="int64"))],
    )
    clean = pa.array([None, 1], type=pa.int64())
    assert (
        typed_storage.Snapshot(pa.table({"a": dirty})).fingerprints
        == typed_storage.Snapshot(pa.table({"a": clean})).fingerprints
    )
    category = pa.array(["a", None, "b"]).dictionary_encode()
    first = typed_storage.Snapshot(pa.table({"a": category}))
    assert first.fingerprints == typed_storage.Snapshot(first.table).fingerprints
    assert first.project(["a"]).fingerprints == first.fingerprints


def test_location_identity_scope_geometry_and_order() -> None:
    table = pa.table({"id": ["a"], "value": [1]})
    geometry = geometry_shapely.ingest_geometries(np.array([Point(1, 2)]))
    scope = typed_storage.Scope(
        "places", "point", "EPSG:4326", "id", "geometry", ("id", "value", "geometry")
    )
    snapshot = typed_storage.Snapshot(table, geometry, scope)
    for changed in (
        replace(scope, collection="other"),
        replace(scope, unit_type="other"),
        replace(scope, crs="EPSG:3857"),
    ):
        assert (
            typed_storage.Snapshot(table, geometry, changed).fingerprints
            != snapshot.fingerprints
        )
    moved = typed_storage.Snapshot(
        table, geometry_shapely.ingest_geometries(np.array([Point(1, 3)])), scope
    )
    assert moved.fingerprints[0] != snapshot.fingerprints[0]
    assert moved.fingerprints[1] == snapshot.fingerprints[1]
    assert (
        typed_storage.Snapshot(table.select(["value", "id"])).fingerprints
        != typed_storage.Snapshot(table).fingerprints
    )


@pytest.mark.parametrize("dtype", [pa.list_(pa.int64()), pa.large_list(pa.int64())])
def test_null_list_identity(dtype: pa.DataType) -> None:
    array = pa.array([None, [1], None], type=dtype)
    snapshot = typed_storage.Snapshot(pa.table({"a": array}))
    chunked = pa.chunked_array([array.slice(0, 1), array.slice(1)])
    assert (
        snapshot.fingerprints
        == typed_storage.Snapshot(pa.table({"a": chunked})).fingerprints
    )
    assert (
        snapshot.slice(0, 1).fingerprints
        == typed_storage.Snapshot(
            pa.table({"a": pa.array([None], type=dtype)})
        ).fingerprints
    )
