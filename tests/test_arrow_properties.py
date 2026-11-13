"""Declared column fidelity through the public location runtime."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pyarrow import array
from shapely.geometry import Point

from lyra.data.location_artifacts import decode, encode
from lyra.data.location_validation import validate_snapshot
from lyra.sdk.location_schema import LocationOutputSchema
from test_support.location_helpers import native_snapshot, output_schema, snapshot_frame
from test_support.workloads import schema as native_schema
from tests.storage_data import frame


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
def test_public_typed_round_trip(state: str) -> None:
    source = gpd.GeoDataFrame(frame(state, spatial=True).drop(columns="category"))
    source["id"] = pd.Series(
        [str(i) for i in range(len(source))], index=source.index, dtype=str
    )
    source = source.drop(columns=["unsigned", "time", "date"])
    source["small"] = source["small"].astype("Int64")
    source["single"] = source["single"].astype("Float64")
    schema = native_schema(source)
    contract = LocationOutputSchema(schema)
    value = contract.prepare_frame(source)
    expected = source[list(schema.columns)].reset_index(drop=True)
    pd.testing.assert_frame_equal(value.copy(deep=True), expected)
    restored = snapshot_frame(validate_snapshot(decode(encode(native_snapshot(value)))))
    pd.testing.assert_frame_equal(restored.copy(deep=True), expected)
    pd.testing.assert_frame_equal(restored, value)
    for subset, indices in [
        (value.iloc[:0], []),
        (
            value.iloc[::-1].reset_index(drop=True),
            list(reversed(range(len(value)))),
        ),
    ]:
        pd.testing.assert_frame_equal(
            subset.copy(deep=True), expected.iloc[indices].reset_index(drop=True)
        )
    names = ["double", "small"]
    pd.testing.assert_frame_equal(value[["id", *names]], expected[["id", *names]])
    if len(source):
        identity = native_snapshot(value).fingerprint
        source.loc[:, "id"] = "changed"
        exported = value.copy(deep=True)
        exported.loc[:, "id"] = "changed"
        assert native_snapshot(value).fingerprint == identity
        pd.testing.assert_frame_equal(value.copy(deep=True), expected)


def test_subsets_keep_contract_and_operation_checks() -> None:

    schema = output_schema(value="Int64")
    schema.schema.checks = [pa.Check(lambda data: len(data) >= 2, name="minimum_rows")]
    source = gpd.GeoDataFrame(
        {"id": ["a", "b"], "value": pd.Series([1, 2], dtype="Int64")},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
    )
    value = schema.prepare_frame(source)
    subset = value.loc[value.id == "b"].reset_index(drop=True)
    assert subset["value"][0] == 2
    assert subset["value"].dtype == pd.Int64Dtype()
    pd.testing.assert_frame_equal(subset, value.iloc[1:].reset_index(drop=True))

    with pytest.raises(ValueError, match="minimum_rows"):
        schema.prepare_frame(subset.copy(deep=True))


def test_string_ids_selection_retains_declared_storage() -> None:
    source = gpd.GeoDataFrame(
        {"id": pd.Series(["a", "b"], dtype="string")},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
    )
    value = output_schema(id=source.id.dtype).prepare_frame(source)
    snapshot = native_snapshot(value)
    for selected, positions in [
        (snapshot.slice(0, 0), []),
        (snapshot.take(array([1, 0])), [1, 0]),
    ]:
        pd.testing.assert_frame_equal(
            snapshot_frame(selected), source.iloc[positions].reset_index(drop=True)
        )
        assert selected.table.schema == snapshot.table.schema
