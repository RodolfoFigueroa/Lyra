"""Declared column fidelity through the public location runtime."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    LocationOutputSchema,
    Locations,
    PluginDefinition,
    arrow_conversion,
    geometry_shapely,
    location_operation,
)
from lyra.sdk.location_artifacts import decode, encode
from lyra.sdk.models.location import location_snapshot, locations_from_snapshot
from pyarrow import Table
from shapely.geometry import Point

from tests.location_helpers import output_schema
from tests.storage_data import declarations, frame


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
def test_public_typed_round_trip(state: str) -> None:
    source = gpd.GeoDataFrame(frame(state, spatial=True).drop(columns="category"))
    source["id"] = pd.Series(
        [str(i) for i in range(len(source))], index=source.index, dtype=str
    )
    schema = (
        declarations(spatial=True)
        .remove_columns(["category"])
        .add_columns(
            {"id": pa.Column(str, description="Identifier", metadata={"unit": None})}
        )
    )
    for column in schema.columns.values():
        column.description = "Value"
        column.metadata = {"unit": None}
    contract = LocationOutputSchema(schema)
    value = Locations.from_geodataframe(
        source, schema=contract, collection="test:units", unit_type="point"
    )
    expected = source[list(schema.columns)].reset_index(drop=True)
    pd.testing.assert_frame_equal(value.to_geodataframe(), expected)
    restored = locations_from_snapshot(decode(encode(location_snapshot(value))))
    pd.testing.assert_frame_equal(restored.to_geodataframe(), expected)
    assert restored == value
    for subset, indices in [
        (value.slice(0, 0), []),
        (
            value.select_ids(list(reversed(value.ids))),
            list(reversed(range(len(value)))),
        ),
    ]:
        pd.testing.assert_frame_equal(
            subset.to_geodataframe(), expected.iloc[indices].reset_index(drop=True)
        )
    names = ["double", "small"]
    pd.testing.assert_frame_equal(
        value.to_dataframe(properties=names), expected[["id", *names]]
    )
    if len(source):
        identity = location_snapshot(value).fingerprint
        source.loc[:, "id"] = "changed"
        exported = value.to_geodataframe()
        exported.loc[:, "id"] = "changed"
        assert location_snapshot(value).fingerprint == identity
        pd.testing.assert_frame_equal(value.to_geodataframe(), expected)


def test_subsets_keep_contract_and_operation_checks() -> None:

    schema = output_schema(value="Int64")
    schema.schema.checks = [pa.Check(lambda data: len(data) >= 2, name="minimum_rows")]
    source = gpd.GeoDataFrame(
        {"id": ["a", "b"], "value": pd.Series([1, 2], dtype="Int64")},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
    )
    value = schema.build(source, collection="test:units", unit_type="point")
    subset = value.select_ids(["b"])
    assert subset.property("value")[0] == 2
    assert subset.to_dataframe()["value"].dtype == pd.Int64Dtype()
    assert subset == value.slice(1)

    @location_operation(
        name="subset",
        description="Select one row",
        output=schema,
        collection="test:units",
        unit_type="point",
    )
    def operation() -> gpd.GeoDataFrame:
        return subset.to_geodataframe()

    plugin = PluginDefinition(location_operations=[operation])
    with pytest.raises(ValueError, match="minimum_rows"):
        plugin.normalize_location_result(
            plugin.prepare_resolved_invocation("subset", {}), operation()
        )


def test_projection_only_converts_selected_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    source = gpd.GeoDataFrame(
        {
            "id": ["a"],
            "time": pd.Series([pd.Timestamp("2026-01-01T00:00:00.123456789Z")]),
            "unused": [7],
        },
        geometry=[Point(1, 2)],
        crs=4326,
    )
    value = output_schema(time=source.time.dtype, unused=int).build(
        source, collection="test:units", unit_type="point"
    )
    original = arrow_conversion.from_arrow
    converted = []

    def convert(table: Table) -> pd.DataFrame:
        converted.append(table.column_names)
        return original(table)

    def forbidden(*_args: object) -> None:
        pytest.fail("Nonspatial projection decoded geometry")

    monkeypatch.setattr(arrow_conversion, "from_arrow", convert)
    monkeypatch.setattr(geometry_shapely, "frame_geometries", forbidden)
    monkeypatch.setattr("lyra.sdk.models.location.frame_geometries", forbidden)
    assert value.property("time")[0] == source.time[0]
    assert converted == []
    pd.testing.assert_frame_equal(
        value.to_dataframe(properties=["time"]), source[["id", "time"]]
    )
    assert converted == [["id", "time"]]


def test_categorical_ids_selection_retains_declared_storage() -> None:
    source = gpd.GeoDataFrame(
        {"id": pd.Categorical(["a", "b"], categories=["b", "a"], ordered=True)},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
    )
    value = output_schema(id=source.id.dtype).build(
        source, collection="test:units", unit_type="point"
    )
    restored = locations_from_snapshot(decode(encode(location_snapshot(value))))
    for candidate in [value, restored]:
        for ids, positions in [(["b", "a"], [1, 0]), (["b"], [1])]:
            selected = candidate.select_ids(ids)
            pd.testing.assert_frame_equal(
                selected.to_geodataframe(),
                source.iloc[positions].reset_index(drop=True),
            )
        empty = candidate.select_ids([])
        assert empty.is_empty
        assert (
            location_snapshot(empty).table.schema
            == location_snapshot(value).table.schema
        )
        assert candidate.select_ids(["b"]) == candidate.slice(1)
        with pytest.raises(KeyError, match="unknown"):
            candidate.select_ids(["unknown"])
        with pytest.raises(ValueError, match="unique"):
            candidate.select_ids(["a", "a"])
