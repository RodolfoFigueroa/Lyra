"""Receiving routes execute the accepted graph with target ownership."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.errors import ExecutionPersistenceError
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.parameters import MetricParameters
from tests.geoparquet_integration_helpers import assert_remote_outputs
from tests.receiving_route_helpers import Trace, definitions, request
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.sqlite_helpers import database

__all__ = ["database", "native_api"]


@pytest.mark.parametrize(
    "binding", ["matching", "explicit", "renamed", "operation_source", "nested", "same"]
)
@pytest.mark.parametrize("development", [False, True])
def test_receiving_routes_and_root_routes(binding: str, *, development: bool) -> None:
    trace = Trace()
    runner = LocalRunner.from_definitions(definitions(trace, binding=binding))
    payload = request()
    original = deepcopy(payload)
    run = runner.run_development if development else runner.run
    result = run(payload.pop("metric"), **payload)
    assert result.tables["total"].total.tolist() == [90]
    assert result.metadata.outputs["total"].owner_scope is None
    assert trace.frames["root"][0].location_id.tolist() == ["a", "b"]
    assert trace.frames["root"][0].value.tolist() == [1, 1]
    measured = trace.frames["measure"][0]
    assert measured.crs == "EPSG:4326"
    assert set(measured.location_id) == (
        {"x", "y", "z"} if binding == "same" else {"X", "Y", "Z"}
    )
    assert set(zip(measured.source_location_id, measured.location_id, strict=True)) == (
        {("X", "x"), ("Y", "y"), ("Z", "z")}
        if binding == "same"
        else {("x", "X"), ("y", "Y"), ("z", "Z")}
    )
    assert all(
        values == [2]
        for name, values in trace.parameters
        if name in {"first", "second"}
    )
    assert all(
        values == [99]
        for name, values in trace.parameters
        if name in {"initial", "explicit", "same"}
    )
    assert ("measure", [3]) in trace.parameters
    assert payload == {key: value for key, value in original.items() if key != "metric"}
    assert trace.events[-1][0] == "close"
    assert len({id(value) for _, value in trace.events}) == 1

    # The same canonical chain is also valid directly at the root boundary.
    direct = run("producer", inputs=original["inputs"], parameters={"weights": [3]})
    assert direct.tables["rows"].value.tolist() == [30, 30, 30]
    scope = direct.metadata.outputs["rows"].owner_scope
    assert scope is not None
    assert scope.provider_id == "target"
    assert trace.events[0][1] is not trace.events[-1][1]


@pytest.mark.parametrize(
    "failure", ["mapping", "missing", "provider", "operation", "metric", "output"]
)
def test_receiving_failure_stops_consumers_and_closes_resources(failure: str) -> None:
    trace = Trace()
    runner = LocalRunner.from_definitions(definitions(trace, failure=failure))
    payload = request()
    payload.pop("metric")
    for _ in range(2):
        trace.events.clear()
        with pytest.raises(GraphExecutionError) as caught:
            runner.run("root", **payload)
        assert caught.value.provenance is not None
        assert not caught.value.provenance.execution.succeeded
        assert "root" not in trace.frames
        assert trace.events[-1][0] == "close"
        assert not trace.events[-1][1].open
        names = [name for name, _ in trace.events]
        assert names.count("close") == 1
        for provider in ("source", "middle", "target"):
            if provider in names:
                assert names.count(f"close:{provider}") == 1
                assert names.index(f"close:{provider}") < names.index("close")


@pytest.mark.parametrize("failure", [None, "omitted", "empty"])
@pytest.mark.parametrize("ids", [(), ("a", "b")])
def test_empty_receiving_routes(failure: str | None, ids: tuple[str, ...]) -> None:
    trace = Trace()
    runner = LocalRunner.from_definitions(definitions(trace, failure=failure))
    payload = request(ids)
    result = runner.run(payload.pop("metric"), **payload)
    assert result.tables["total"].total.tolist() == [
        90 if ids and failure is None else 0
    ]
    if not ids or failure:
        assert "measure" not in trace.frames
        assert "second" not in trace.frames
        assert "acquired" not in trace.frames
        assert "root" in trace.frames  # Needed unpartitioned summaries still execute.
    if not ids:
        assert trace.events == []
        assert "first" not in trace.frames


def test_accepted_route_defaults_and_cache_survive_handler_mutation(
    tmp_path: Path,
) -> None:
    accepting = True

    def defaults() -> list[int]:
        assert accepting, "Defaults ran after acceptance"
        return [2]

    class Parameters(MetricParameters):
        weights: list[int] = Field(default_factory=defaults, description="Weights")

    trace = Trace()
    runner = LocalRunner.from_definitions(
        definitions(trace, policy="full", options=Parameters)
    )
    plan = prepare_execution(runner.catalog, request())
    original = plan.model_dump()
    accepting = False
    executor = GraphExecutor(
        runner.catalog,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    first = executor.execute(plan)
    trace.parameters.clear()
    trace.events.clear()
    second = executor.execute(plan)
    pd.testing.assert_frame_equal(first.outputs["total"], second.outputs["total"])
    assert second.outputs["total"].total.tolist() == [90]
    assert trace.parameters == []
    assert "measure" not in [name for name, _ in trace.events]
    assert "open:handler" not in [name for name, _ in trace.events]
    assert plan.model_dump() == original
    assert {
        entry.node
        for entry in second.provenance.execution.observations
        if entry.outcome == "cache_hit"
    } == {"producer"}


def test_equal_provider_uses_no_automatic_operation() -> None:
    trace = Trace()
    runner = LocalRunner.from_definitions(definitions(trace))
    result = runner.run(
        "producer",
        inputs={"places": {"provider": "target", "ids": ["X", "Y"]}},
        parameters={"weights": [3]},
    )
    assert result.tables["rows"].value.tolist() == [30, 30]
    assert trace.parameters == [("measure", [3])]
    assert "source_location_id" not in trace.frames["measure"][0]


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.usefixtures("database")
def test_retained_final_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_api: NativeAPI,
    *,
    development: bool,
) -> None:

    trace = Trace()
    path = tmp_path / "route.sqlite"
    plugins = definitions(trace)
    for name, provider in plugins["providers"].resources.items():
        monkeypatch.setattr(provider, "description", f"Captured {name}")
    runner = LocalRunner.from_definitions(plugins, result_store_path=path)
    payload = request()
    run = runner.run_development if development else runner.run
    result = run("producer", inputs=payload["inputs"], parameters={"weights": [3]})
    path.touch()
    before = len(trace.events)
    with ResultStore.open(path) as reader:
        captured = reader.describe_result(result.metadata.result_ref).location_inputs[
            "places"
        ]
        assert captured.provider_scope.description == "Captured target"
        assert captured.source_scope is not None
        assert captured.source_scope.provider_id == "middle"
        assert captured.source_scope.description == "Captured middle"
        saved = reader.read_location_input(result.metadata.result_ref, "places")
        assert set(zip(saved.source_location_id, saved.location_id, strict=True)) == {
            ("x", "X"),
            ("y", "Y"),
            ("z", "Z"),
        }
        destination = tmp_path / "route.parquet"
        reader.export_location_input_geoparquet(
            result.metadata.result_ref, "places", destination
        )
        exported = gpd.read_parquet(destination)
        pd.testing.assert_frame_equal(exported, saved)
        assert exported.attrs["lyra"]["description"] == captured.model_dump(mode="json")
        assert saved.crs == "EPSG:4326"
        assert "row_key" not in saved
    assert_remote_outputs(
        path, [result, result], native_api, monkeypatch, exports_only=True
    )
    assert len(trace.events) == before


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("writer", ["pairs", "records"])
def test_route_retention_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:

    target = OperationPairs if writer == "pairs" else ProviderRecords
    monkeypatch.setattr(target, "publish", Mock(side_effect=OSError("write failed")))
    runner = LocalRunner.from_definitions(
        definitions(Trace()), result_store_path=tmp_path / "failed.sqlite"
    )
    with pytest.raises(ExecutionPersistenceError):
        runner.run("producer", inputs=request()["inputs"], parameters={"weights": [3]})
