import json

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pyproj
import pytest

from lyra.data import ipc_codec
from lyra.data.typed_storage import Snapshot
from lyra.sdk.storage_schema import arrow_schema
from tests.location_artifact_helpers import alter
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
            source, arrow_schema(schema), collection="places", unit_type="point"
        )
    else:
        snapshot = Snapshot.from_frame(source, arrow_schema(schema))
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
    snapshot = Snapshot.from_frame(frame("populated"), arrow_schema(declarations()))
    pd.testing.assert_frame_equal(
        ipc_codec.decode(ipc_codec.encode(snapshot)).to_frame(), snapshot.to_frame()
    )


@pytest.mark.parametrize("change", ["truncate", "payload"])
def test_corruption(change: str) -> None:
    payload = bytearray(
        ipc_codec.encode(Snapshot.from_frame(frame(), arrow_schema(declarations())))
    )
    if change == "truncate":
        del payload[-20:]
    else:
        payload[-20] ^= 1
    with pytest.raises(ValueError, match="typed IPC artifact"):
        ipc_codec.decode(payload)


def test_inconsistent_reconstructed_column_names() -> None:
    original = Snapshot.from_frame(frame(), arrow_schema(declarations()))
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


@pytest.mark.parametrize("field", ["version", "fingerprints", "scope"])
def test_invalid_metadata(field: str) -> None:

    original = ipc_codec.encode(
        Snapshot.from_frame(frame(), arrow_schema(declarations()))
    )

    def change(metadata: dict, table: pa.Table, geometry: bytes) -> tuple:
        metadata[field] = 999
        return metadata, table, geometry

    with pytest.raises(ValueError, match="typed IPC artifact"):
        ipc_codec.decode(alter(original, change))


@pytest.mark.parametrize("damage", ["pandas", "nullability"])
def test_malformed_type_metadata_and_nullability(damage: str) -> None:
    original = ipc_codec.encode(
        Snapshot.from_frame(frame(), arrow_schema(declarations()))
    )

    def change(metadata: dict, table: pa.Table, geometry: bytes) -> tuple:
        if damage == "pandas":
            table = table.replace_schema_metadata(
                {**table.schema.metadata, b"pandas": b"invalid json"}
            )
        else:
            table = pa.Table.from_arrays(
                [pa.array([None], type=pa.int64())],
                schema=pa.schema(
                    [pa.field("value", pa.int64(), nullable=False)],
                    metadata=table.schema.metadata,
                ),
            )
        return metadata, table, geometry

    with pytest.raises(ValueError, match=r"typed IPC artifact.*(Expecting|Nulls)"):
        ipc_codec.decode(alter(original, change))


def test_standard_geoarrow_reader() -> None:
    source = gpd.GeoDataFrame(
        {"id": ["a", "b"], "value": [1.0, 2.0]},
        geometry=gpd.points_from_xy([1.0, 3.0], [2.0, 4.0]),
        crs="EPSG:4326",
    )
    schema = pandera.DataFrameSchema(
        {"id": pandera.Column(str), "value": pandera.Column(float)}
    )
    snapshot = Snapshot.from_frame(source, arrow_schema(schema))
    encoded = ipc_codec.encode(snapshot)
    table = pa.ipc.open_file(encoded).read_all()
    restored = gpd.GeoDataFrame.from_arrow(table)
    pd.testing.assert_frame_equal(restored, source)
    assert (
        table.schema.field("geometry").metadata[b"ARROW:extension:name"]
        == b"geoarrow.wkb"
    )


@pytest.mark.parametrize(
    "crs",
    [
        "+proj=aeqd +lat_0=37 +lon_0=-122 +datum=WGS84 +units=m +no_defs",
        "+proj=longlat +ellps=clrk66 +no_defs",
    ],
)
@pytest.mark.parametrize("representation", ["proj", "wkt", "json"])
@pytest.mark.parametrize("metric", [False, True])
def test_custom_crs_round_trip(crs: str, representation: str, *, metric: bool) -> None:
    reference = pyproj.CRS(crs)
    crs = {"proj": crs, "wkt": reference.to_wkt(), "json": reference.to_json()}[
        representation
    ]
    source = gpd.GeoDataFrame(
        {"id": ["a"]}, geometry=gpd.points_from_xy([1.0], [2.0]), crs=crs
    )
    schema = pa.schema([("id", pa.string())])
    snapshot = (
        Snapshot.from_frame(source, schema)
        if metric
        else Snapshot.from_locations(
            source, schema, collection="places", unit_type="point"
        )
    )
    encoded = ipc_codec.encode(snapshot)
    restored = ipc_codec.decode(encoded)
    assert restored.scope == snapshot.scope
    assert restored.fingerprints == snapshot.fingerprints
    assert restored.scope is not None
    assert pyproj.CRS(restored.scope.crs) == reference
    pd.testing.assert_frame_equal(
        gpd.GeoDataFrame.from_arrow(pa.ipc.open_file(encoded).read_all()), source
    )


def test_changed_geometry_rejected() -> None:
    source = gpd.GeoDataFrame(
        {"id": ["a"]}, geometry=gpd.points_from_xy([1.0], [2.0]), crs=4326
    )
    snapshot = Snapshot.from_frame(
        source, arrow_schema(pandera.DataFrameSchema({"id": pandera.Column(str)}))
    )

    def change(metadata: dict, table: pa.Table, geometry: bytes) -> tuple:
        replacement = gpd.GeoSeries(gpd.points_from_xy([5.0], [6.0])).to_wkb()
        table = table.set_column(
            1, table.schema.field(1), pa.array(replacement, type=pa.binary())
        )
        return metadata, table, geometry

    with pytest.raises(ValueError, match="fingerprint"):
        ipc_codec.decode(alter(ipc_codec.encode(snapshot), change))
