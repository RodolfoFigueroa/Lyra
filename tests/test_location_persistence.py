"""Typed location contents survive standalone and cached consumption."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk import read_location_artifact
from lyra.sdk.cache import TableCache
from lyra.sdk.location_artifacts import encode
from lyra.sdk.models.location import location_snapshot

from benchmarks.schema_storage.fixtures import declarations, frame
from tests.test_location_schema import column, contract, declaration


@pytest.mark.parametrize("state", ["populated", "empty", "all-null", "indexed"])
def test_typed_persistence_and_selection(tmp_path: Path, state: str) -> None:
    properties = declarations().columns
    schema = contract(
        declaration(
            **{
                name: column(value.dtype, nullable=True)
                for name, value in properties.items()
            }
        )
    )
    source = frame(state, spatial=True)
    assert isinstance(source, gpd.GeoDataFrame)
    source = source.rename_geometry("shape")
    source.insert(
        0,
        "key",
        pd.Series(
            [str(i) for i in range(len(source))], index=source.index, dtype="str"
        ),
    )
    fresh = schema.build(source, collection="example:places", unit_type="point")
    path = tmp_path / "standalone.arrow"
    path.write_bytes(encode(location_snapshot(fresh)))
    standalone = read_location_artifact(path).to_locations()
    cache = TableCache(tmp_path / "cache")
    with cache.lock("locations"):
        cache.write_locations("locations", fresh)
    restored = cache.read_locations("locations")
    assert restored is not None
    expected = fresh.to_geodataframe(id_column="key", geometry_column="shape")
    for value in (standalone, restored):
        assert value == fresh
        assert (
            location_snapshot(value).fingerprints
            == location_snapshot(fresh).fingerprints
        )
        exported = value.to_geodataframe(id_column="key", geometry_column="shape")
        pd.testing.assert_frame_equal(exported, expected)
        assert exported.crs == expected.crs
        for selected, original in (
            (value.slice(0, 1), fresh.slice(0, 1)),
            (
                value.select_ids(list(value.ids)[::-1]),
                fresh.select_ids(list(fresh.ids)[::-1]),
            ),
            (value.slice(0, 0), fresh.slice(0, 0)),
        ):
            assert selected == original
            pd.testing.assert_frame_equal(
                selected.to_dataframe(), original.to_dataframe()
            )
        if len(exported):
            exported.loc[0, "signed"] = 5
            exported.loc[0, "key"] = "mutated"
        pd.testing.assert_frame_equal(
            value.to_geodataframe(id_column="key", geometry_column="shape"), expected
        )
    assert cache.read_locations("locations") == fresh
