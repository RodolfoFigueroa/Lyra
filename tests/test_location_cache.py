"""Completed mappings remain separate from authoritative provider records."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.sdk import loading
from lyra.server import worker
from lyra.server.plugins import InstalledPlugin
from tests.config_helpers import load_test_config
from tests.location_cache_graph import SELECTION, setup_graph
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.worker_graph_integration_helpers import native_job


def run(runner: LocalRunner, label: str) -> None:
    runner.run("root", inputs=SELECTION, parameters={"label": label})


@pytest.mark.parametrize("empty", [False, True])
def test_fresh_call_reuses_completed_mapping(tmp_path: Path, *, empty: bool) -> None:
    runner, cache, key, events, seen, _ = setup_graph(tmp_path, empty=empty)
    run(runner, "a")
    saved = cache.read_operation(key)
    assert saved is not None
    assert saved.value.groups == {"0": () if empty else ("0",)}
    assert saved.value.source_scope.provider_scope is not None
    assert saved.value.target_scope.provider_scope is not None
    assert saved.value.source_scope.provider_scope.provider_id == "cache_source"
    assert saved.value.target_scope.provider_scope.provider_id == "cache_target"
    events.clear()
    run(runner, "b")
    assert events == ["b"]
    assert len(seen) == 2
    for frame in seen:
        assert frame.location_id.tolist() == ([] if empty else ["0"])
        assert frame.source_location_id.tolist() == frame.location_id.tolist()
        assert frame["count"].tolist() == ([] if empty else [2])


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "json",
        "kind",
        "members",
        "scope",
        "keys",
        "type",
        "duplicate",
        "completion",
    ],
)
def test_invalid_entries_recompute(tmp_path: Path, damage: str) -> None:
    runner, cache, key, events, _, _ = setup_graph(tmp_path)
    run(runner, "a")
    directory = cache.store.directory / str(cache.reference(key))
    path = directory / "operation.json"
    value = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_text("broken")
    elif damage in {"kind", "members"}:
        manifest = directory / "manifest.json"
        data = json.loads(manifest.read_text())
        data[damage] = "metric" if damage == "kind" else ["../operation.json"]
        manifest.write_text(json.dumps(data))
    else:
        if damage == "scope":
            value["source_scope"] = value["target_scope"]
        else:
            value["groups"] = {
                "keys": {"wrong": ["0"]},
                "type": {"0": [1]},
                "duplicate": {"0": ["0", "0"]},
                "completion": {},
            }[damage]
        path.write_text(json.dumps(value))
    events.clear()
    run(runner, "b")
    assert events == ["make", "b"]
    saved = cache.read_operation(key)
    assert saved is not None
    assert saved.value.groups == {"0": ("0",)}


@pytest.mark.parametrize(
    "failure", ["read_operation", "write_validated_operation", "lock"]
)
def test_storage_failures_are_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    runner, _, _, events, seen, _ = setup_graph(tmp_path)
    if failure == "read_operation":
        run(runner, "a")
        events.clear()
    failed = Mock(side_effect=OSError("unavailable"))
    if failure == "lock":
        monkeypatch.setattr("lyra.runtime.cache.FileLock", failed)
    else:
        monkeypatch.setattr(ArtifactStore, failure, failed)
    run(runner, "b")
    assert failed.called
    assert "make" in events
    assert seen[-1]["count"].tolist() == [2]


def test_failed_consumer_preserves_operation(tmp_path: Path) -> None:
    runner, cache, key, events, _, broken = setup_graph(tmp_path)
    broken[0] = True
    with pytest.raises(GraphExecutionError):
        run(runner, "a")
    assert cache.read_operation(key) is not None
    broken[0] = False
    events.clear()
    run(runner, "a")
    assert events == ["a"]


def test_invalid_operation_is_not_published(tmp_path: Path) -> None:
    runner, cache, key, events, _, broken = setup_graph(tmp_path)
    broken[1] = True
    with pytest.raises(GraphExecutionError, match="tuple"):
        run(runner, "a")
    assert cache.read_operation(key) is None
    broken[1] = False
    events.clear()
    run(runner, "a")
    assert "make" in events


def test_publication_failure_leaves_no_partial_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, cache, key, _, seen, _ = setup_graph(tmp_path)
    monkeypatch.setattr(Path, "rename", Mock(side_effect=OSError("disk failure")))
    run(runner, "a")
    assert seen[0]["count"].tolist() == [2]
    assert cache.read_operation(key) is None
    assert list(cache.store.directory.iterdir()) == []


def test_worker_jobs_reuse_internal_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(loading, "load_plugin_definition", lambda _: definition)
    config = load_test_config(tmp_path)
    config.artifact_dir = tmp_path / "cache"
    worker.set_runner_temp_base(tmp_path / "scratch")
    try:
        for label in ("a", "b"):
            worker.refresh_runner_registry("heavy", config=config)
            native = native_job(monkeypatch)
            native.id = label
            inputs = SELECTION | {"parameters": {"label": label}}

            result = run_and_read(
                worker.run_metric_task,
                accepted_envelope(
                    job_id=label, metric="root", inputs=inputs
                ).model_dump(mode="json"),
            )
            assert result["outputs"] == {"root": [{"value": 1}]}
        assert calls == ["make", "source", "a", "b"]
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)


def test_mapping_arriving_under_lock_skips_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, cache, key, events, seen, _ = setup_graph(tmp_path)
    run(runner, "a")
    saved = cache.read_operation(key)
    assert saved is not None
    (cache.directory / key).unlink()
    original = TableCache.lock
    active = []

    @contextmanager
    def lock(self: TableCache, candidate: str) -> Generator[bool, None, None]:
        assert not active, "Dependent invocation locks must not overlap"
        with original(self, candidate) as acquired:
            active.append(candidate)
            try:
                if candidate == key:
                    cache.write_operation(key, saved.value)
                yield acquired
            finally:
                active.pop()

    monkeypatch.setattr(TableCache, "lock", lock)
    events.clear()
    run(runner, "b")
    assert events == ["b"]
    assert seen[-1]["count"].tolist() == [2]
