"""Combined fidelity through runtime selections, standalone reads, and cache hits."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    LocalRunner,
    LocationOutputSchema,
    Locations,
    PluginDefinition,
    metric,
    read_location_artifact,
)
from lyra.sdk.cache import TableCache
from lyra.sdk.models.location import location_fingerprint

from benchmarks.schema_storage.fixtures import declarations, frame
from benchmarks.schema_storage.run import assert_equal


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
def test_location_fidelity_across_boundaries(tmp_path: Path, state: str) -> None:
    # Empty dictionary restoration is a separate native categorical limitation.
    schema = declarations(spatial=True).remove_columns(["category"])
    schema = schema.add_columns({"id": pa.Column(str)})
    for name, column in schema.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    output = LocationOutputSchema(schema)
    source = gpd.GeoDataFrame(frame(state, spatial=True).drop(columns="category"))
    source["id"] = pd.array([f"row-{i}" for i in range(len(source))], dtype="str")
    original = source.copy(deep=True)
    expected = source[list(schema.columns)].reset_index(drop=True)
    value = Locations.from_geodataframe(
        source, collection="acceptance:places", unit_type="point", schema=output
    )
    assert_equal(source, original)
    cache = TableCache(tmp_path)
    with cache.lock("matrix"):
        cache.write_locations("matrix", value)
    restored = cache.read_locations("matrix")
    assert restored is not None
    artifact = read_location_artifact(tmp_path / "matrix" / "locations.arrow")
    for result in (value, restored, artifact.to_locations()):
        assert location_fingerprint(result) == location_fingerprint(value)
        assert output.validate(result) is result
        assert_equal(result.to_geodataframe(), expected)
        for subset, rows in (
            (result.slice(0, 0), []),
            (result.slice(1, 1), list(range(len(expected)))[1:2]),
            (
                result.select_ids(list(reversed(result.ids))),
                list(reversed(range(len(expected)))),
            ),
        ):
            subset_expected = expected.iloc[rows].reset_index(drop=True)
            assert_equal(subset.to_geodataframe(), subset_expected)
            assert subset.collection == value.collection
            assert subset.unit_type == value.unit_type
        exported = result.to_geodataframe()
        if len(exported):
            exported.loc[0, "signed"] = 0
            source.loc[source.index[0], "signed"] = 0
        assert_equal(result.to_geodataframe(), expected)
        assert location_fingerprint(result) == location_fingerprint(value)
        assert isinstance(result.to_dataframe().index, pd.RangeIndex)


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
@pytest.mark.parametrize("spatial", [False, True])
def test_metric_fidelity_reaches_consumer(
    tmp_path: Path, state: str, *, spatial: bool
) -> None:
    schema = declarations(spatial=spatial).remove_columns(["category"])
    for name, column in schema.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    produced = frame(state, spatial=spatial).drop(columns="category")
    expected = produced[list(schema.columns)].reset_index(drop=True)
    calls = []

    @metric(name="source", description="Source", outputs={"rows": schema})
    def source() -> pd.DataFrame:
        calls.append("source")
        return produced

    @metric(
        name="consumer",
        description="Consumer",
        outputs={"result": schema},
        inputs={"rows": Dependency("rows")},
    )
    def consumer(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("consumer")
        assert_equal(rows, expected)
        return rows

    runner = LocalRunner.from_definitions(
        {"acceptance": PluginDefinition(metrics=[source, consumer])}, cache_dir=tmp_path
    )
    assert_equal(runner.run("source", {})["rows"], expected)
    assert calls == ["source"]
    assert_equal(runner.run("consumer", {})["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(runner.run("consumer", {})["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(produced, frame(state, spatial=spatial).drop(columns="category"))
