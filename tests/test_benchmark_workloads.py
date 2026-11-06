"""Correctness and isolation of the permanent performance workloads."""

import geopandas as gpd
import pandas as pd
import pytest
import shapely
from geopandas.testing import assert_geodataframe_equal

from lyra.data import ipc_codec
from lyra.data.typed_storage import Snapshot
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.storage_schema import arrow_schema
from test_support.location_workloads import location_workload
from test_support.preparation_workload import preparation_workload
from test_support.storage_workloads import CASES, storage_frame
from test_support.workloads import frame, schema, workload


@pytest.mark.parametrize(("shape", "increment"), [("chain", 6), ("diamond", 4)])
def test_repeated_graph_execution(shape: str, increment: int) -> None:
    work = workload(7, shape)
    runner = LocalRunner.from_definitions({"benchmark-fixture": work.plugin})
    expected = frame(7)
    expected["value"] = (
        expected.value + increment
        if shape == "chain"
        else expected.value * 4 + increment
    )
    for run in range(2):
        pd.testing.assert_frame_equal(runner.run("root", {})["root"], expected)
        assert work.calls["source"] == run + 1


def test_preparation_without_provider() -> None:
    compiled, request = preparation_workload(3)
    prepared = prepare_execution(compiled, request)
    assert [item.name for item in prepared.inputs] == ["left", "right"]
    assert prepared.inputs[0].ids == ("0", "1", "2")
    with pytest.raises(PreparationError):
        prepare_execution(compiled, request | {"unexpected": 1})


def test_location_execution() -> None:
    runner, inputs = location_workload(3)
    result = runner.run("root", inputs)
    pd.testing.assert_frame_equal(result["root"], frame(6))


@pytest.mark.parametrize("kind", ["mixed", "polygon"])
def test_storage_roundtrip_and_fingerprints(kind: str) -> None:
    table = frame(17, kind)
    snapshot = Snapshot.from_frame(table, arrow_schema(schema(table)))
    first = snapshot.fingerprints
    assert snapshot.fingerprints is first
    restored = ipc_codec.decode(ipc_codec.encode(snapshot))
    assert restored.fingerprints == first


@pytest.mark.parametrize("case", CASES)
def test_representative_storage(case: str) -> None:
    original = storage_frame(case, rows=24)
    repeated = storage_frame(case, rows=24)
    snapshot = Snapshot.from_frame(original, arrow_schema(schema(original)))
    restored = ipc_codec.decode(ipc_codec.encode(snapshot))
    assert snapshot.fingerprints == restored.fingerprints
    for actual in [repeated, restored.to_frame()]:
        if isinstance(original, gpd.GeoDataFrame):
            assert isinstance(actual, gpd.GeoDataFrame)
            assert_geodataframe_equal(actual, original)
            assert shapely.equals_identical(
                actual.geometry.to_numpy(), original.geometry.to_numpy()
            ).all()
            assert shapely.is_valid(actual.geometry.to_numpy()).all()
        else:
            pd.testing.assert_frame_equal(actual, original)
    assert original["count"].isna().sum() == 3
    if case == "mixed-geometries":
        assert len(set(original.geometry.geom_type)) == 6
        assert original.geometry.has_z.sum() == 12
    if case.startswith("complex-"):
        assert shapely.get_num_coordinates(original.geometry.iloc[0]) == 1090
    if case == "polygon-holes":
        assert shapely.get_num_coordinates(original.geometry.iloc[0]) == 82
    if case == "multipolygons":
        assert shapely.get_num_geometries(original.geometry.iloc[0]) == 3
