"""Deterministic investigation mechanics without timing thresholds or services."""

from pathlib import Path
from unittest.mock import patch

import pytest
from lyra.sdk import content_fingerprints

from tests.storage_cases import Case, fixture
from tests.storage_operations import Work


@pytest.mark.parametrize("kind", ["native", "points", "polygons", "rich"])
@pytest.mark.parametrize("width", [1, 4])
def test_declared_cases(kind: str, width: int) -> None:
    data = fixture(Case("test", 7, kind, 16, width))
    data.schema.validate(data.frame)
    work = Work(data)
    work.check_frame(work.snapshot)
    if kind not in {"rich", "native"}:
        assert len(data.frame.columns) == 9 * width + 2


@pytest.mark.parametrize("kind", ["native", "points", "polygons", "rich"])
def test_boundaries(tmp_path: Path, kind: str) -> None:
    work = Work(fixture(Case("test", 7, kind, 16)))
    for name in work.operations():
        operation = work.prepare(name, tmp_path / name.replace(":", "-"))
        operation.verify(operation.action())


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
