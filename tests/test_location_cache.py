"""Internal JSON artifacts and persistent mixed-graph reuse."""

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import (
    Dependency,
    LocalRunner,
    LocationDependency,
    Locations,
    PluginDefinition,
    location_operation,
    metric,
    plugin_loader,
)
from lyra.sdk.cache import TableCache
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.results import NativeOutputs

from lyra_app import worker
from lyra_app.plugins import InstalledPlugin
from tests.config_helpers import load_test_config, worker_credentials
from tests.test_graph_execution import Label
from tests.test_location_schema import column, contract, declaration, locations
from tests.test_table_cache import SCHEMA
from tests.test_worker_graph_integration import native_job


def setup_graph(
    tmp_path: Path, *, empty: bool = False
) -> tuple[LocalRunner, TableCache, str, list[str], list[Locations], list[bool]]:
    events = []
    seen = []
    broken = [False, False]
    output = contract(declaration(count=column("Int64", coerce=True)))

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [2]})

    @location_operation(
        name="make",
        description="Make",
        output=output,
        inputs={"table": Dependency("source")},
    )
    def make(table: pd.DataFrame) -> Locations:
        events.append("make")
        if broken[1]:
            return locations({"count": "invalid"})
        return locations() if empty else locations({"count": str(table.value[0])})

    @metric(name="consume", description="Consume", outputs={"consume": SCHEMA})
    def consume(location: Locations, parameters: Label) -> pd.DataFrame:
        events.append(parameters.label)
        seen.append(location)
        if broken[0]:
            return pd.DataFrame({"wrong": [1]})
        return pd.DataFrame({"value": [len(location.ids)]})

    @metric(
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "table": Dependency(
                "consume", locations={"location": LocationDependency("make")}
            )
        },
    )
    def root(parameters: Label, table: pd.DataFrame) -> pd.DataFrame:
        assert parameters.label
        return table

    plugin = PluginDefinition(
        metrics=[root, consume, source], location_operations=[make]
    )
    runner = LocalRunner.from_definitions({"p": plugin}, cache_dir=tmp_path)
    cache = TableCache(tmp_path)
    key = cache.key(plugin.prepare_invocation("make", {}).identity)
    return runner, cache, key, events, seen, broken


def run(runner: LocalRunner, label: str) -> NativeOutputs:
    return runner.run("root", {"parameters": {"label": label}})


@pytest.mark.parametrize("empty", [False, True])
def test_fresh_call_reuses_operation_and_skips_tables(
    tmp_path: Path, *, empty: bool
) -> None:
    runner, cache, key, events, seen, _ = setup_graph(tmp_path, empty=empty)
    run(runner, "a")
    assert events == ["source", "make", "a"]
    expected = locations() if empty else locations({"count": 2})
    assert seen == [expected]
    assert cache.read_locations(key) == expected
    # Leave only the operation artifact: a hit must skip its table dependency.
    for entry in tmp_path.iterdir():
        if entry.is_dir() and entry.name != key:
            shutil.rmtree(entry)
    events.clear()
    run(runner, "b")
    assert events == ["b"]
    assert seen == [expected, expected]


def damage_payload(payload: dict, damage: str) -> None:
    value = payload["locations"]
    feature = value["features"][0]
    if damage == "kind":
        payload["kind"] = "metric"
    elif damage == "id":
        feature["id"] = ""
    elif damage == "duplicate":
        value["features"].append(feature)
    elif damage == "geometry":
        feature["geometry"]["coordinates"] = [float("inf"), 2]
    elif damage == "property":
        feature["properties"]["count"] = []
    elif damage == "missing_property":
        feature["properties"] = {}
    elif damage == "extra_property":
        feature["properties"]["extra"] = 1
    elif damage == "schema":
        feature["properties"]["count"] = "invalid"
    else:
        value["crs"]["properties"]["name"] = "invalid"


@pytest.mark.parametrize(
    "damage",
    [
        "json",
        "missing",
        "kind",
        "table",
        "id",
        "duplicate",
        "geometry",
        "property",
        "missing_property",
        "extra_property",
        "schema",
        "crs",
    ],
)
def test_invalid_entries_recompute(tmp_path: Path, damage: str) -> None:
    runner, cache, key, events, _, _ = setup_graph(tmp_path)
    run(runner, "a")
    path = tmp_path / key / "locations.json"
    payload = json.loads(path.read_text())
    if damage == "json":
        path.write_text("{")
    elif damage == "missing":
        path.unlink()
    elif damage == "table":
        with cache.lock(key):
            cache.write(key, {"make": pd.DataFrame({"value": [1]})})
    else:
        damage_payload(payload, damage)
        path.write_text(json.dumps(payload))
    events.clear()
    run(runner, "b")
    assert events == ["make", "b"]
    assert cache.read_locations(key) == locations({"count": 2})


def test_cached_coercion_reaches_consumers(tmp_path: Path) -> None:
    runner, cache, key, events, seen, _ = setup_graph(tmp_path)
    with cache.lock(key):
        cache.write_locations(key, locations({"count": "7"}))
    run(runner, "a")
    assert events == ["a"]
    assert seen == [locations({"count": 7})]


@pytest.mark.parametrize("empty", [False, True])
def test_json_preserves_custom_columns_and_temporal_categories(
    tmp_path: Path, *, empty: bool
) -> None:
    schema = contract(
        declaration(
            category=column(pd.CategoricalDtype([date(2026, 1, 1)]), nullable=True),
            when=column(pd.DatetimeTZDtype(tz="UTC"), nullable=True),
            count=column("Int64", nullable=True),
        )
    )
    value = (
        locations()
        if empty
        else locations(
            {"category": None, "when": None, "count": None},
            {"category": "2026-01-01", "when": "2026-01-01T12:00:00+00:00", "count": 3},
        )
    )
    value.features.reverse()
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        cache.write_locations("key", schema.validate(value))
    decoded = cache.read_locations("key")
    assert decoded is not None
    assert schema.validate(decoded) == value
    assert cache.read("key") is None


@pytest.mark.parametrize("failure", ["read_locations", "write_locations", "lock"])
def test_storage_failures_are_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    runner, _, _, events, seen, _ = setup_graph(tmp_path)
    monkeypatch.setattr(TableCache, failure, Mock(side_effect=OSError("unavailable")))
    run(runner, "a")
    assert events == ["source", "make", "a"]
    assert seen == [locations({"count": 2})]


def test_failed_consumer_preserves_operation(tmp_path: Path) -> None:
    runner, cache, key, events, _, broken = setup_graph(tmp_path)
    broken[0] = True
    with pytest.raises(GraphExecutionError):
        run(runner, "a")
    assert cache.read_locations(key) == locations({"count": 2})
    broken[0] = False
    events.clear()
    run(runner, "a")
    assert events == ["a"]


def test_locked_recheck_and_nonnested_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, cache, operation_key, events, seen, _ = setup_graph(tmp_path)
    original = TableCache.lock
    active = []

    @contextmanager
    def lock(self: TableCache, key: str) -> Iterator[bool]:
        assert not active
        if key == operation_key:
            assert events == ["source"]
        with original(self, key) as acquired:
            active.append(key)
            try:
                if key == operation_key:
                    cache.write_locations(key, locations({"count": 9}))
                yield acquired
            finally:
                active.pop()

    monkeypatch.setattr(TableCache, "lock", lock)
    run(runner, "a")
    assert events == ["source", "a"]
    assert seen == [locations({"count": 9})]


def test_worker_jobs_reuse_internal_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @location_operation(name="make", description="Make", output=contract(declaration()))
    def make() -> Locations:
        calls.append("make")
        return locations({})

    @metric(name="consume", description="Consume", outputs={"consume": SCHEMA})
    def consume(location: Locations, parameters: Label) -> pd.DataFrame:
        calls.append(parameters.label)
        assert location == locations({})
        return pd.DataFrame({"value": [1]})

    @metric(
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "table": Dependency(
                "consume", locations={"location": LocationDependency("make")}
            )
        },
    )
    def root(parameters: Label, table: pd.DataFrame) -> pd.DataFrame:
        assert parameters.label
        return table

    definition = PluginDefinition(metrics=[root, consume], location_operations=[make])
    manifest = definition.manifest(
        plugin=PluginInfo(name="mixed", version="1"), factory="mixed:create"
    )
    installed = [
        InstalledPlugin("mixed", manifest, {"root": "heavy", "consume": "heavy"})
    ]
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(plugin_loader, "load_plugin_definition", lambda _: definition)
    config = load_test_config(tmp_path)
    config.cache_dir = tmp_path / "cache"
    worker.set_runner_temp_base(tmp_path / "scratch")
    try:
        for label in ("a", "b"):
            worker.refresh_runner_registry(
                "heavy", config=config, credentials=worker_credentials()
            )
            native = native_job(monkeypatch)
            native.id = label
            inputs = {"parameters": {"label": label}}
            native.meta["provenance"]["input"] = inputs
            result = worker.run_metric_task(
                JobEnvelope(job_id=label, metric="root", input=inputs).model_dump(
                    mode="json"
                )
            )
            assert result["outputs"] == {"root": [{"value": 1}]}
        assert calls == ["make", "a", "b"]
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)


def test_invalid_operation_is_not_published(tmp_path: Path) -> None:
    runner, cache, key, events, _, broken = setup_graph(tmp_path)
    broken[1] = True
    with pytest.raises(GraphExecutionError, match="invalid result"):
        run(runner, "a")
    assert cache.read_locations(key) is None
    broken[1] = False
    events.clear()
    run(runner, "a")
    assert events == ["make", "a"]


def test_location_publication_failure_cleans_partial_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, cache, key, events, seen, _ = setup_graph(tmp_path)
    monkeypatch.setattr(Path, "rename", Mock(side_effect=OSError("disk failure")))
    run(runner, "a")
    assert events == ["source", "make", "a"]
    assert seen == [locations({"count": 2})]
    assert cache.read_locations(key) is None
    assert not list(tmp_path.glob(".writing-*"))
