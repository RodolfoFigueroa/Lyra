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


def test_native_operations_without_authoring_or_execution() -> None:
    assert_fresh_imports(
        MODULES,
        blocked=BLOCKED,
        assertions="""
        import geopandas as gpd
        from shapely.geometry import MultiPoint, Point
        from lyra.data.location_frames import validate_frame
        frame = gpd.GeoDataFrame(
            {'id': ['a', 'b'], 'value': [1, 2]},
            geometry=[Point(1, 2, 3), MultiPoint([])], crs=4326,
        )
        validate_frame(frame, 'id')
        owned = frame.copy(deep=True)
        frame.loc[0, 'value'] = 99
        assert owned['value'].tolist() == [1, 2]
        assert owned.geometry.iloc[0].has_z
        assert owned.geometry.iloc[1].is_empty
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
