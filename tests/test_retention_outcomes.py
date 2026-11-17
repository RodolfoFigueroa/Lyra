"""Published values survive failures and policy changes without run history."""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from rq.job import JobStatus
from typing_extensions import override

from lyra.contracts.config import JobsConfig
from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.resources import IOConfig
from lyra.data.relational import StorageDescription
from lyra.data.results import NativeOutputs
from lyra.runtime.execution import ExecutionOutcome, GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk import Dependency, LocationDependency, PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from lyra.server import job_store
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import Database, database, fail_insert

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def graph(
    tmp_path: Path, events: list, fault: list[str], policy: CachePolicy
) -> GraphExecutor:
    class Support(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            events.append("setup")
            if fault == ["acquisition"]:
                raise RuntimeError(fault[0])

        @override
        def close(self) -> None:
            events.append("close")

    @metric(
        name="source",
        description="Source",
        cache=policy,
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"data": schema()},
    )
    def source(places: gpd.GeoDataFrame) -> pd.DataFrame:
        events.append(("source", frozenset(places.location_id)))
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "row", "value": 1}
        )

    @metric(
        name="consumer",
        description="Consumer",
        cache="full",
        inputs={
            "places": LocationDependency(provider="target", primary=True),
            "data": Dependency("data", producer="source"),
        },
        outputs={"data": schema()},
    )
    def consumer(
        places: gpd.GeoDataFrame,
        data: pd.DataFrame,
        support: Annotated[BaseIO, Resource("support")],
    ) -> pd.DataFrame:
        assert isinstance(support, Support)
        assert set(places.location_id) == set(data.location_id)
        events.append("consumer")
        if fault == ["consumer"]:
            raise RuntimeError(fault[0])
        return data

    definitions = provider_execution_definitions(events)
    definitions["test"] = PluginDefinition(
        metrics=[source, consumer], resources={"support": Support}
    )
    local = LocalRunner.from_definitions(definitions)
    return GraphExecutor(
        local.catalog,
        local.catalog.implementations,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
        retain_result=True,
    )


def run(
    executor: GraphExecutor, node: str = "consumer", ids: tuple[str, ...] = ("a", "b")
) -> ExecutionOutcome:
    return executor.execute(
        prepare_execution(
            executor.catalog,
            {
                "metric": node,
                "inputs": {"places": {"provider": "target", "ids": list(ids)}},
            },
        )
    )


@pytest.mark.parametrize(
    "fault_name", ["consumer", "acquisition", "publication", "finalization"]
)
def test_retention_after_failure(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch, fault_name: str
) -> None:
    events: list = []
    fault = [fault_name]
    executor = graph(tmp_path, events, fault, "full")
    publish = MetricTables.publish

    def fail_publish(
        self: MetricTables,
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
        outputs: NativeOutputs,
        descriptions: Mapping[str, StorageDescription],
        *,
        location_inputs: Mapping[str, InputBindings],
    ) -> None:
        if selections[0][0].node_id == "consumer":
            # Fail the transaction itself, after dependency publication committed.
            database.fail_commit = True
        return publish(
            self, selections, outputs, descriptions, location_inputs=location_inputs
        )

    with monkeypatch.context() as patch:
        if fault_name == "publication":
            patch.setattr(MetricTables, "publish", fail_publish)
        if fault_name == "finalization":
            fail_insert(database, patch, "final_results", RuntimeError("finalization"))
        with pytest.raises(GraphExecutionError):
            run(executor)
    assert executor.provenance is not None
    assert not executor.provenance.execution.succeeded
    assert events.count("setup") == events.count("close") == 1
    assert {
        r["node_id"]
        for r in database.rows["stored_results"]
        if r["node_id"] != "target"
    } == ({"source", "consumer"} if fault_name == "finalization" else {"source"})
    # Intermediate cached computations retain bindings before becoming a root.
    assert executor.store is not None
    retained = LocationInputs(executor.store)
    with executor.store.engine.connect() as connection:
        for row in database.rows["stored_results"]:
            if row["node_id"] == "source":
                bindings = retained.bindings(connection, row["value_key"])
                assert bindings.names == ("places",)
                restored = retained.frame(
                    connection, bindings.selected().selections["places"]
                )
                assert set(restored.location_id) == {"a", "b"}
    fault.clear()
    database.fail_commit = False
    events.clear()
    # Fresh executor: neither historical provenance nor invocation metadata exists.
    retry = graph(tmp_path, events, fault, "full")
    result = run(retry)
    assert set(result.outputs["data"].location_id) == {"a", "b"}
    assert not any(isinstance(event, tuple) for event in events)
    assert events.count("consumer") == (0 if fault_name == "finalization" else 1)
    assert events.count("setup") == events.count("close")
    events.clear()
    run(retry)
    assert events == []


def test_policy_round_trip_retains_complete_and_partition_values(
    tmp_path: Path, database: Database
) -> None:
    events: list = []
    keys = set()
    complete_key = None
    policies: list[tuple[CachePolicy, bool]] = [
        ("off", True),
        ("full", False),
        ("off", True),
        ("per_location", True),
        ("full", False),
        ("per_location", False),
    ]
    for policy, executes in policies:
        events.clear()
        executor = graph(tmp_path, events, [], policy)
        result = run(executor, "source")
        assert bool([e for e in events if isinstance(e, tuple)]) == executes
        current = {
            r["value_key"]
            for r in database.rows["stored_results"]
            if r["node_id"] != "target"
        }
        assert keys <= current
        keys = current
        computations = dict(result.completions[0].computations)
        if policy != "per_location":
            complete_key = complete_key or computations[None]
            assert computations == {None: complete_key}
        else:
            assert set(computations) == {"a", "b"}
            assert complete_key not in computations.values()
    assert len(keys) == 3


def test_downstream_hit_bypasses_off_source_and_disabled_provider(
    tmp_path: Path,
) -> None:
    events: list = []
    executor = graph(tmp_path, events, [], "off")
    run(executor)
    events.clear()
    result = run(executor)
    assert events == []
    assert [(o.node, o.outcome) for o in result.provenance.execution.observations] == [
        ("consumer", "cache_hit")
    ]
    run(executor, "source")
    assert ("source", frozenset({"a", "b"})) in events
    assert "resolve:target" in events


def test_partial_reuse_observed_and_resources_closed(tmp_path: Path) -> None:
    events: list = []
    executor = graph(tmp_path, events, [], "per_location")
    run(executor, "source", ("a",))
    events.clear()
    result = run(executor, "source")
    assert ("source", frozenset({"b"})) in events
    assert [(o.node, o.outcome) for o in result.provenance.execution.observations] == [
        ("source", "partial_hit"),
        ("source", "started"),
        ("source", "completed"),
    ]
    assert events.count("setup:target") == events.count("close:target") == 1


def test_expired_job_does_not_expire_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list = []
    first = run(graph(tmp_path, events, [], "full"))
    jobs = job_store.JobStore(Mock(ttl=Mock(return_value=-2)), JobsConfig())
    expired = Mock(
        id="job", key="rq:job:job", get_status=Mock(return_value=JobStatus.FINISHED)
    )
    monkeypatch.setattr(job_store.Job, "fetch", Mock(return_value=expired))
    assert jobs.read_job("job") is None
    events.clear()
    # Cache matching reads only values and their descriptions, never the job.
    monkeypatch.setattr(
        job_store.Job, "fetch", Mock(side_effect=AssertionError("history consulted"))
    )
    result = run(graph(tmp_path, events, [], "full"))
    assert events == []
    assert result.result_ref != first.result_ref
    assert set(result.outputs["data"].location_id) == {"a", "b"}


@pytest.mark.parametrize("policy", ["full", "per_location"])
@pytest.mark.parametrize("failed", [False, True])
def test_intermediate_inputs_reused_as_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: CachePolicy,
    *,
    failed: bool,
) -> None:
    events: list = []
    producer = graph(tmp_path, events, ["consumer"] if failed else [], policy)
    if failed:
        with pytest.raises(GraphExecutionError):
            run(producer)
    else:
        run(producer)
    events.clear()
    executor = graph(tmp_path, events, [], policy)
    with monkeypatch.context() as patch:
        patch.setattr(LocationInputs, "rows", Mock(side_effect=AssertionError))
        result = run(executor, "source")
    assert events == []
    assert set(result.outputs["data"].location_id) == {"a", "b"}
    assert executor.store is not None
    inputs = LocationInputs(executor.store)
    with executor.store.engine.connect() as connection:
        for completion in result.completions:
            for _, key in completion.computations:
                bindings = inputs.bindings(connection, key)
                saved = inputs.frame(
                    connection, bindings.selected().selections["places"]
                )
                assert set(saved.location_id) <= {"a", "b"}
                assert not saved.empty
