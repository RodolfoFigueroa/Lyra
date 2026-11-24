"""Completed mappings remain separate from authoritative provider records."""

from pathlib import Path

import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from lyra.sdk import loading
from lyra.server import worker
from lyra.server.plugins import InstalledPlugin
from tests.sqlite_helpers import Database, database

__all__ = ["database"]

from tests.config_helpers import load_test_config
from tests.location_cache_graph import SELECTION, setup_graph
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.worker_graph_integration_helpers import native_job


def run(runner: LocalRunner, label: str) -> None:
    runner.run("root", inputs=SELECTION, parameters={"label": label})


@pytest.mark.parametrize("empty", [False, True])
def test_fresh_call_reuses_completed_mapping(
    database: Database, tmp_path: Path, *, empty: bool
) -> None:
    runner, _cache, key, events, seen, _ = setup_graph(tmp_path, empty=empty)
    run(runner, "a")
    assert database.rows["stored_results"]
    store = SQLiteStore(tmp_path / "values.sqlite")
    with store.engine.connect() as connection:
        header = store.header(connection, key)
    saved = OperationPairs(store).lookup(header, ("0",))
    assert saved == {"0": () if empty else ("0",)}
    assert header.owner_scope is not None
    assert header.target_scope is not None
    assert header.owner_scope.provider_id == "cache_source"
    assert header.target_scope.provider_id == "cache_target"
    events.clear()
    run(runner, "b")
    assert events == ["b"]
    assert len(seen) == 2
    for frame in seen:
        assert frame.location_id.tolist() == ([] if empty else ["0"])
        assert frame.source_location_id.tolist() == frame.location_id.tolist()
        assert frame["count"].tolist() == ([] if empty else [2])


def test_worker_jobs_reuse_internal_locations(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _, _, calls, _, _ = setup_graph(tmp_path)
    definition = runner.catalog.implementations["p"].definition
    installed = [
        InstalledPlugin(
            "mixed",
            definition,
            {"root": "heavy", "consume": "heavy", "source": "heavy"},
        )
    ]
    monkeypatch.setattr(
        "lyra.server.plugins.discover_installed_plugins", lambda _: installed
    )
    monkeypatch.setattr(loading, "load_plugin_definition", lambda _: definition)
    config = load_test_config(tmp_path)
    config.result_store_path = tmp_path / "cache"
    worker.set_runner_temp_base(tmp_path / "scratch")
    try:
        for label in ("a", "b"):
            worker.initialize_worker("heavy", config=config)
            native = native_job(monkeypatch)
            native.id = label * 32
            inputs = SELECTION | {"parameters": {"label": label}}

            result = run_and_read(
                worker.run_metric_task,
                accepted_envelope(
                    job_id=native.id, metric="root", inputs=inputs
                ).model_dump(mode="json"),
            )
            assert result["outputs"] == {"root": [{"value": 1}]}
        assert database.rows["stored_results"]
        assert calls == ["make", "source", "a", "b"]
    finally:
        worker.set_runner_temp_base(None)


def test_failed_consumer_preserves_operation(
    database: Database, tmp_path: Path
) -> None:
    runner, _, key, events, _, broken = setup_graph(tmp_path)
    broken[0] = True
    with pytest.raises(GraphExecutionError):
        run(runner, "a")
    assert any(row["value_key"] == key for row in database.rows["stored_results"])
    broken[0] = False
    events.clear()
    run(runner, "a")
    assert events == ["a"]


def test_invalid_operation_is_not_published(database: Database, tmp_path: Path) -> None:
    runner, _, key, events, _, broken = setup_graph(tmp_path)
    broken[1] = True
    with pytest.raises(GraphExecutionError, match="tuple"):
        run(runner, "a")
    assert not any(
        row["value_key"] == key for row in database.rows.get("stored_results", [])
    )
    broken[1] = False
    events.clear()
    run(runner, "a")
    assert "make" in events


def test_damaged_completion_does_not_rerun_handler(
    database: Database, tmp_path: Path
) -> None:
    runner, _, _, events, _, _ = setup_graph(tmp_path)
    run(runner, "a")
    database.rows["stored_partitions"].clear()
    events.clear()
    with pytest.raises(
        GraphExecutionError, match="Damaged operation mapping"
    ) as raised:
        run(runner, "b")
    assert isinstance(raised.value.__cause__, DamagedDataError)
    assert "make" not in events


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_empty_source_storage(
    database: Database, tmp_path: Path, policy: CachePolicy
) -> None:
    runner, _, _, events, _, _ = setup_graph(tmp_path, policy=policy, ids=())
    runner.run(
        "root",
        inputs={"seed": {"provider": "cache_source", "ids": []}},
        parameters={"label": "a"},
    )
    assert "make" not in events
    headers = [
        row
        for row in database.rows.get("stored_results", [])
        if row["node_id"] == "make"
    ]
    # Complete empty correspondences support downstream identity even with off.
    assert len(headers) == (0 if policy == "per_location" else 1)
    assert not database.rows.get("stored_partitions")


def test_partition_keys_survive_batch_changes(
    database: Database, tmp_path: Path
) -> None:
    observed = []
    for ids in (("0", "1"), ("0", "2"), ("0",)):
        runner, _, _, events, _, _ = setup_graph(
            tmp_path, policy="per_location", ids=ids
        )
        runner.run(
            "root",
            inputs={"seed": {"provider": "cache_source", "ids": list(ids)}},
            parameters={"label": str(ids)},
        )
        observed.append(events)
    assert "make" in observed[0]
    assert "make" in observed[1]
    assert "make" not in observed[2]
    assert (
        len(
            [row for row in database.rows["stored_results"] if row["node_id"] == "make"]
        )
        == 3
    )
    keys = {
        row["value_key"]
        for row in database.rows["stored_results"]
        if row["node_id"] == "make"
    }
    assert sorted(
        row["location_id"]
        for row in database.rows["stored_partitions"]
        if row["value_key"] in keys
    ) == [
        "0",
        "1",
        "2",
    ]
