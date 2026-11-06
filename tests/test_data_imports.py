from pathlib import Path

import pytest

from test_support.imports import assert_fresh_imports

DATA = Path("src/lyra/data")
MODULES = sorted(
    "lyra.data" + ("" if path.stem == "__init__" else f".{path.stem}")
    for path in DATA.glob("*.py")
)
BLOCKED = ("lyra.sdk", "lyra.runtime", "lyra.server", "pandera")


@pytest.mark.parametrize("module", MODULES)
def test_data_import_is_independent(module: str) -> None:
    assert_fresh_imports([module], blocked=BLOCKED)


def test_data_facade_exports_native_value() -> None:
    assert_fresh_imports(
        ["lyra.data"],
        blocked=BLOCKED,
        assertions="""
        from lyra.data import Locations
        assert Locations.__module__ == 'lyra.data.locations'
        """,
    )


def test_native_operations_without_authoring_or_execution() -> None:
    assert_fresh_imports(
        MODULES,
        blocked=BLOCKED,
        assertions="""
        import numpy as np
        import pandas as pd
        import geopandas as gpd
        import pyarrow as pa
        from shapely.geometry import MultiPoint, Point
        from lyra.data.location_frames import validate_frame
        from lyra.data.location_snapshot import LocationSnapshot
        from lyra.data.geometry_shapely import frame_geometries
        from lyra.data.scalars import normalize_property
        from lyra.data.typed_storage import Snapshot

        frame = gpd.GeoDataFrame(
            {'id': ['a', 'b'], 'value': [1, 2]},
            geometry=[Point(1, 2, 3), MultiPoint([])], crs=4326,
        )
        validate_frame(frame, 'id')
        schema = pa.schema([('id', pa.string()), ('value', pa.int64())])
        snapshot = LocationSnapshot.from_locations(
            frame, schema, collection='test:places', unit_type='point'
        )
        from lyra.data import Locations
        from lyra.data.locations import locations_from_snapshot, location_fingerprint
        value = locations_from_snapshot(snapshot)
        assert type(value) is Locations
        assert list(value.ids) == ['a', 'b']
        assert list(value.select_ids(['b', 'a']).ids) == ['b', 'a']
        assert location_fingerprint(value) == snapshot.fingerprint
        assert value.to_geodataframe().equals(frame)
        identity = snapshot.fingerprints
        frame.loc[0, 'value'] = 99
        assert snapshot.table['value'].to_pylist() == [1, 2]
        assert snapshot.fingerprints == identity
        assert frame_geometries(snapshot)[0].equals_exact(Point(1, 2, 3), 0)
        assert frame_geometries(snapshot)[0].has_z
        assert frame_geometries(snapshot)[1].is_empty
        assert snapshot.slice(1).table['id'].to_pylist() == ['b']
        assert snapshot.take(pa.array([1, 0])).table['id'].to_pylist() == ['b', 'a']
        assert snapshot.project(['value']).to_frame()['value'].tolist() == [1, 2]
        assert snapshot.property_data()['properties'] == [{'value': 1}, {'value': 2}]
        assert normalize_property(np.int64(3), 'value') == 3
        assert normalize_property(pd.NA, 'value') is None
        values = np.array([1, 2], dtype='int64')
        owned = Snapshot(pa.table({'value': values}))
        values[0] = 99
        assert owned.table['value'].to_pylist() == [1, 2]
        """,
    )


def test_native_results_without_authoring_or_execution() -> None:
    assert_fresh_imports(
        ["lyra.data.results", "lyra.data.inspection"],
        blocked=BLOCKED,
        assertions="""
        from unittest.mock import patch
        import pandas as pd
        import geopandas as gpd
        from shapely.geometry import Point
        from lyra.contracts.errors import MetricResultError
        from lyra.contracts.inspection import MAX_INSPECTION_BYTES, json_size
        from lyra.data.results import serialize_outputs, validate_serializable
        from lyra.data.inspection import summarize_outputs

        frame = pd.DataFrame({'value': range(1000), 'text': ['a' * 600] * 1000})
        spatial = gpd.GeoDataFrame(
            {'value': [1]}, geometry=[Point(111319.49079327357, 0)], crs=3857
        )
        validate_serializable(frame)
        validate_serializable(spatial)
        outputs = {'ordinary': frame, 'spatial': spatial}
        encoded = serialize_outputs('test', outputs)
        feature = encoded['spatial']['features'][0]
        assert abs(feature['geometry']['coordinates'][0] - 1) < 1e-9
        assert 'id' not in feature
        encoded['ordinary'][0]['value'] = -1
        assert frame.iloc[0]['value'] == 0
        assert serialize_outputs('test', outputs)['ordinary'][0]['value'] == 0
        assert spatial.crs.to_epsg() == 3857

        def bounded_encode(node, tables):
            assert sum(len(table) for table in tables.values()) <= 5
            assert all(len(table.columns) <= 21 for table in tables.values())
            return serialize_outputs(node, tables)

        with patch(
            'lyra.data.inspection.serialize_outputs', side_effect=bounded_encode
        ):
            summary = summarize_outputs(outputs)
        assert summary['ordinary']['metadata']['summary']['value']['maximum'] == 999
        assert summary['ordinary']['truncation']['omitted_rows'] == 995
        assert summary['ordinary']['truncation']['shortened_strings'] == 5
        assert summary['spatial']['preview']['features'] == []
        assert json_size(summary) <= MAX_INSPECTION_BYTES

        try:
            unknown_crs = spatial.set_crs(None, allow_override=True)
            serialize_outputs('test', {'bad': unknown_crs})
        except MetricResultError as exc:
            assert exc.metric == 'test'
            assert exc.path == "output 'bad' serialization"
        else:
            raise AssertionError('Unknown CRS accepted')

        for invalid in [pd.DataFrame({'value': [float('inf')]}),
                        pd.DataFrame({'value': pd.Categorical([1, float('inf')])})]:
            try:
                validate_serializable(invalid)
            except ValueError as exc:
                assert exc.column == 'value'
                assert exc.check == 'finite'
            else:
                raise AssertionError('Infinite value accepted')
        """,
    )


def test_artifact_read_and_export_without_authoring_or_execution() -> None:
    assert_fresh_imports(
        ["lyra.data"],
        blocked=BLOCKED,
        assertions="""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import geopandas as gpd
        import pandas as pd
        import pyarrow as pa
        from shapely.geometry import Point
        from lyra.data import (
            ArtifactDecodeError, LocationArtifact, export_locations,
            read_location_artifact,
        )
        from lyra.data.location_artifacts import encode
        from lyra.data.location_snapshot import LocationSnapshot

        assert LocationArtifact.__module__ == 'lyra.data.artifact_reader'
        assert ArtifactDecodeError.__module__ == 'lyra.data.location_artifacts'
        frame = gpd.GeoDataFrame(
            {'id': ['a', 'b'], 'count': pd.Series([2**60 + 1, None], dtype='Int64')},
            geometry=[Point(1, 2, 3), Point(4, 5, 6)], crs=4326,
        )
        snapshot = LocationSnapshot.from_locations(
            frame, pa.schema([('id', pa.string()), ('count', pa.int64())]),
            collection='test:places', unit_type='point',
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.arrow'
            source.write_bytes(encode(snapshot))
            artifact = read_location_artifact(source)
            assert type(artifact) is LocationArtifact
            assert artifact.metadata['producer_schema_validated'] is False
            preview = artifact.preview(1)
            assert len(preview) == 1
            preview[0]['properties']['count'] = 0
            assert artifact.preview(1)[0]['properties']['count'] == 2**60 + 1
            restored = artifact.to_locations().to_geodataframe()
            pd.testing.assert_frame_equal(restored, frame)
            restored.loc[0, 'count'] = 0
            pd.testing.assert_frame_equal(
                artifact.to_locations().to_geodataframe(), frame
            )
            output = export_locations(artifact, root / 'export')
            pd.testing.assert_frame_equal(
                gpd.read_parquet(output / 'locations.parquet'), frame
            )
            try:
                export_locations(artifact, output)
            except FileExistsError:
                pass
            else:
                raise AssertionError('Existing output overwritten')
            source.write_bytes(b'unsupported')
            try:
                read_location_artifact(source)
            except ArtifactDecodeError:
                pass
            else:
                raise AssertionError('Invalid artifact accepted')
        """,
    )
