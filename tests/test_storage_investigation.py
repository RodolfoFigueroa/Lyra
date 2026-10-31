"""Deterministic investigation mechanics without timing thresholds or services."""

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from lyra.sdk import content_fingerprints

from benchmarks.schema_storage import investigate
from benchmarks.schema_storage.investigate import parser, worker
from benchmarks.schema_storage.investigation_cases import Case, cases, fixture
from benchmarks.schema_storage.investigation_report import (
    cache_mix,
    load_timings,
    render,
)
from benchmarks.schema_storage.investigation_runtime import profile, sample, save
from benchmarks.schema_storage.investigation_work import Operation, Work

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess


@pytest.mark.parametrize("kind", ["native", "points", "polygons", "rich"])
@pytest.mark.parametrize("width", [1, 4])
def test_declared_cases(kind: str, width: int) -> None:
    data = fixture(Case("test", 7, kind, 16, width))
    data.schema.validate(data.frame)
    work = Work(data)
    work.check_frame(work.snapshot)
    if kind not in {"rich", "native"}:
        assert len(data.frame.columns) == 9 * width + 2
    assert len({case.name for case in cases()}) == len(cases()) == 16


@pytest.mark.parametrize("kind", ["native", "points", "polygons", "rich"])
def test_boundaries(tmp_path: Path, kind: str) -> None:
    work = Work(fixture(Case("test", 7, kind, 16)))
    for name in work.operations():
        operation = work.prepare(name, tmp_path / name.replace(":", "-"))
        record = sample(operation)
        assert "seconds" in record
        assert "cpu_seconds" in record


@pytest.mark.parametrize("shape", ["chain", "fanout", "shared"])
@pytest.mark.parametrize("kind", ["native", "points"])
def test_cache_states(tmp_path: Path, shape: str, kind: str) -> None:
    work = Work(fixture(Case("test", 3, kind)))
    for mode in ("off", "cold", "full", "intermediate"):
        operation = work.prepare_graph(shape, mode, tmp_path / mode)
        result = operation.action()
        operation.verify(result)
        if mode == "full":
            assert operation.metadata["handlers"] == []
        elif mode == "intermediate":
            assert "source" not in operation.metadata["handlers"]
            assert "step" not in operation.metadata["handlers"]


@pytest.mark.parametrize("name", ["hash", "encode", "publish"])
def test_first_use_and_reuse(tmp_path: Path, name: str) -> None:
    work = Work(fixture(Case("test", 3, "points")))
    for reuse in (False, True):
        operation = work.prepare(
            f"{name}_{'reuse' if reuse else 'first'}", tmp_path / str(reuse)
        )
        assert operation.metadata["identity_cached_before"] is reuse
        with patch.object(
            content_fingerprints,
            "typed_snapshot",
            wraps=content_fingerprints.typed_snapshot,
        ) as compute:
            result = operation.action()
            assert compute.call_count == (0 if reuse else 1)
        operation.verify(result)


def test_timer_excludes_verification() -> None:
    events = []
    ticks = iter([10.0, 12.0])

    def clock() -> float:
        events.append("clock")
        return next(ticks)

    operation = Operation(
        lambda: events.append("action"), lambda _: events.append("verify")
    )
    assert sample(operation, clock=clock)["seconds"] == pytest.approx(2.0)
    assert events == ["clock", "action", "clock", "verify"]
    operation.verify = lambda _: pytest.fail("Invalid result")
    with pytest.raises(pytest.fail.Exception):
        sample(operation)


def test_partial_failure_preserves_samples(tmp_path: Path) -> None:
    args = argparse.Namespace(
        output=tmp_path,
        case=["native-100"],
        stage="timing",
        batch=0,
        seed=914,
        samples=3,
        batches=3,
        operation=["hash_reuse", "unknown"],
    )
    with pytest.raises(ValueError, match="Unknown operation"):
        worker(args)
    result = json.loads((tmp_path / "native-100-timing-0.json").read_text())
    assert result["status"] == "failed"
    assert len(result["records"]) == 1
    assert result["records"][0]["repeat"] == 0
    assert "Unknown operation" in result["error"]
    assert not list(tmp_path.glob("*.partial"))


def test_profile_and_output(tmp_path: Path) -> None:
    work = Work(fixture(Case("test", 3, "points")))
    result = profile(work.prepare("hash_first", tmp_path), tmp_path / "case.prof")
    assert (tmp_path / "case.prof").stat().st_size > 0
    assert any(
        row["function"] == "typed_snapshot" and row["calls"] == 1
        for row in result["functions"]
    )
    save(tmp_path / "result.json", result)
    assert json.loads((tmp_path / "result.json").read_text()) == result
    assert parser().parse_args(["--output", str(tmp_path)]).samples == 60


def test_summary_excludes_failed_batches_and_weights_means(tmp_path: Path) -> None:

    records = [
        {
            "operation": f"graph:shared:{mode}",
            "seconds": seconds,
            "cpu_seconds": seconds / 2,
        }
        for mode, seconds in (("cold", 10), ("full", 1), ("intermediate", 5))
    ]
    batch = {"case": {"name": "test"}, "status": "passed", "records": records}
    save(tmp_path / "test-timing-0.json", batch)
    broken = {**batch, "case": {"name": "broken"}}
    save(tmp_path / "broken-timing-0.json", broken)
    save(tmp_path / "broken-timing-1.json", {**broken, "status": "failed"})
    grouped = load_timings(tmp_path)
    assert all(len(rows) == 1 for rows in grouped.values())
    mixes = cache_mix(grouped, "test")
    assert mixes["fresh-heavy"]["seconds"] == pytest.approx(8.6)
    assert mixes["fresh-heavy"]["cpu_seconds"] == pytest.approx(4.3)
    render(tmp_path)
    result = json.loads((tmp_path / "summary.json").read_text())
    assert len(result["timings"]) == 3
    assert all(row["n"] == 1 for row in result["timings"])
    save(tmp_path / "test-timing-1.json", {**batch, "status": "stopped"})
    assert all(len(rows) == 2 for rows in load_timings(tmp_path).values())


def test_memory_barrier_acknowledges_each_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "case.ready"
    ready.write_text(json.dumps({"operation": "hash_first", "VmRSS": 50}))

    class Child:
        calls = 0

        def is_alive(self) -> bool:
            self.calls += 1
            if self.calls == 2:
                assert ready.with_suffix(".go").exists()
                ready.unlink()
            return self.calls < 3

    monkeypatch.setattr(investigate, "rss_sum", lambda _: 100)
    rows = investigate.observe_memory(cast("BaseProcess", Child()), ready)
    assert rows == [{"operation": "hash_first", "VmRSS": 50, "sampled_peak_rss": 100}]
    assert ready.with_suffix(".ack").exists()


def test_resume_keeps_completed_checkpoints(tmp_path: Path) -> None:
    args = argparse.Namespace(output=tmp_path, resume=True, seed=914)
    original = {
        "configuration": {"seed": 914},
        "environment": {"versions": {}},
        "children": [{"batch": 0}],
    }
    save(tmp_path / "manifest.json", original)
    current = {
        "configuration": {"seed": 914, "batches": 2, "samples": 40},
        "environment": {"versions": {}},
    }
    resumed = investigate.resume_manifest(args, current)
    assert resumed["children"] == original["children"]
    assert resumed["resumptions"][-1]["configuration"]["samples"] == 40
    path = tmp_path / "case-timing-0.json"
    save(path, {"status": "passed"})
    assert investigate.completed(tmp_path, "case", "timing", 0)
    save(path, {"status": "running"})
    assert not investigate.completed(tmp_path, "case", "timing", 0)
    assert not investigate.completed(tmp_path, "case", "timing", 1)
    args.seed = 915
    with pytest.raises(AssertionError, match="seed"):
        investigate.resume_manifest(args, current)
