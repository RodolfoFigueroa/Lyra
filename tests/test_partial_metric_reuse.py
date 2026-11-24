"""Partition reuse preserves requested owners, shared context, and completion."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point

from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.result_store import ResultStore
from lyra.runtime.results import RunResult
from lyra.sdk import LocationDependency, LocationProvider, PluginDefinition, metric
from lyra.sdk.storage_schema import empty_frame
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize(
    "policies", [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize(
    "empty_owners", [frozenset(), frozenset({"b"}), frozenset({"a", "b", "c"})]
)
def test_partial_reuse(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    policies: tuple[bool, bool],
    empty_owners: frozenset[str],
) -> None:
    calls = []
    acquisitions = []
    broken = False
    declaration = schema(spatial=policies[0])

    @metric(
        name="partial",
        description="Partial",
        cache="per_location",
        inputs={
            "places": LocationDependency(provider="target", primary=True),
            "reference": LocationDependency(provider="source"),
        },
        outputs={"a": declaration, "b": declaration},
    )
    def partial(places: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> dict:
        calls.append((frozenset(places.location_id), frozenset(reference.location_id)))
        rows = [
            {"location_id": owner, "row_key": row, "value": len(reference)}
            for owner in places.location_id
            if owner not in empty_owners
            for row in ("first", "second")
        ]
        frame = pd.DataFrame(rows) if rows else empty_frame(declaration)
        if rows:
            frame["value"] = pd.array(frame.value.tolist(), dtype="Int64")
        if policies[0] and rows:
            frame = gpd.GeoDataFrame(
                frame, geometry=[Point(0, 0)] * len(frame), crs=4326
            )
        return {"a": frame} if broken else {"a": frame, "b": frame.copy()}

    definitions = provider_execution_definitions([])
    target = definitions["providers"].resources["target_binding"]
    assert issubclass(target, LocationProvider)
    original = target.resolve

    def resolve(self: LocationProvider, ids: list[str]) -> gpd.GeoDataFrame:
        acquisitions.append(frozenset(ids))
        return original(self, ids)

    monkeypatch.setattr(target, "resolve", resolve)
    monkeypatch.setattr(target, "cache", policies[1])
    definitions["partial"] = PluginDefinition(metrics=[partial])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "values"
    )

    def run(ids: list[str], refs: list[str] | None = None) -> RunResult:
        return runner.run(
            "partial",
            inputs={
                "places": {"provider": "target", "ids": ids},
                "reference": {
                    "provider": "source",
                    "ids": refs if refs is not None else ["a", "b", "c"],
                },
            },
        )

    first = run(["a", "b"])
    _assert_work(calls, {"a", "b"})
    assert set().union(*acquisitions) == {"a", "b"}
    calls.clear()
    acquisitions.clear()
    second = run(["b", "c"])
    _assert_work(calls, {"c"})
    assert set().union(*acquisitions) == {"c"}
    calls.clear()
    with monkeypatch.context() as patch:
        patch.setattr(
            LocationResolver,
            "resolve_prepared",
            Mock(side_effect=AssertionError("acquisition on hit")),
        )
        third = run(["c", "b"])
        headers = len(database.rows["stored_results"])
        zero = run([])
        assert len(database.rows["stored_results"]) == headers
    assert calls == []
    for result, owners in (
        (first, {"a", "b"}),
        (second, {"b", "c"}),
        (third, {"b", "c"}),
        (zero, set()),
    ):
        _assert_result(result, owners - empty_owners, owners)
    broken = True
    with pytest.raises(GraphExecutionError):
        run(["b", "d"])
    broken = False
    calls.clear()
    assert "d" in set(run(["b", "d"]).tables["a"].location_id)
    _assert_work(calls, {"d"})
    calls.clear()
    run(["b"], refs=["a"])
    _assert_work(calls, {"b"}, {"a"})


def _assert_work(
    calls: list[tuple[frozenset[str], frozenset[str]]],
    owners: set[str],
    references: set[str] | None = None,
) -> None:
    assert set().union(*(primary for primary, _ in calls)) == owners
    assert all(
        shared == (references if references is not None else {"a", "b", "c"})
        for _, shared in calls
    )


def _assert_result(result: RunResult, owners: set[str], inputs: set[str]) -> None:
    for name in ("a", "b"):
        frame = result.tables[name]
        assert set(zip(frame.location_id, frame.row_key, strict=True)) == {
            (owner, row) for owner in owners for row in ("first", "second")
        }
        owner_scope = result.metadata.outputs[name].owner_scope
        assert owner_scope is not None
        assert owner_scope.provider_id == "target"
    result.metadata.store_path.touch()
    _assert_inputs(result, inputs)
    with ResultStore.open(result.metadata.store_path) as store:
        restored = store.read_result(result.metadata.result_ref)
    for name in ("a", "b"):
        assert_logical_equal(result.tables[name], restored.tables[name])


def _assert_inputs(result: RunResult, owners: set[str]) -> None:
    if not owners:
        return
    with ResultStore.open(result.metadata.store_path) as store:
        places = store.read_location_input(result.metadata.result_ref, "places")
        reference = store.read_location_input(result.metadata.result_ref, "reference")
        assert set(places.location_id) == owners
        assert set(reference.location_id) == {"a", "b", "c"}
        assert len(reference) == 3
        places.loc[:, "location_id"] = "changed"
        assert (
            set(
                store.read_location_input(
                    result.metadata.result_ref, "places"
                ).location_id
            )
            == owners
        )


def test_each_fresh_miss_batch_is_validated_once(tmp_path: Path) -> None:
    checked = []
    declaration = schema()

    def check_batch(data: pd.DataFrame) -> bool:
        if data.attrs.get("fresh"):
            checked.append(set(data.location_id))
        return True

    declaration.checks = [pa.Check(check_batch, name="check_batch")]

    @metric(
        name="sample",
        description="Sample requested owners",
        cache="per_location",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": declaration},
    )
    def sample(places: gpd.GeoDataFrame) -> pd.DataFrame:
        data = pd.DataFrame(
            {"location_id": places.location_id, "row_key": "r", "value": 1}
        )
        data.attrs["fresh"] = True
        return data

    definitions = provider_execution_definitions([])
    definitions["sample"] = PluginDefinition(metrics=[sample])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "sample.sqlite"
    )
    for ids in (["a", "b"], ["b", "c"]):
        result = runner.run(
            "sample", inputs={"places": {"provider": "target", "ids": ids}}
        )
        assert set(result.tables["rows"].location_id) == set(ids)
    assert checked == [{"a", "b"}, {"c"}]
