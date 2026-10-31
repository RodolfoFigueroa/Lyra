"""Deterministic acceptance checks; timing thresholds belong outside tests."""

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from lyra.sdk import content_fingerprints, read_location_artifact
from lyra.sdk.cache import TableCache
from lyra.sdk.models.location import locations_from_snapshot

from benchmarks.location_baseline.acceptance import measure
from benchmarks.location_baseline.fixtures import assert_exact, payload
from tests.location_helpers import stored_locations

if TYPE_CHECKING:
    from lyra.sdk.location_snapshot import LocationSnapshot


def test_integrated_counts_and_recomputation() -> None:
    samples = measure(7, 1, diagnostic=True)
    assert len(samples) == 30
    for sample in samples:
        counts = sample["counts"]
        if sample.get("cache") == "populated":
            assert sample["handlers"] == []
            assert counts.get("content_fingerprints.py:typed_snapshot", 0) == 1
            assert counts.get("arguments.py:prepare_dependency", 0) == 0
        if sample["operation"] in {"fingerprint_reuse", "invocation"}:
            assert counts.get("content_fingerprints.py:typed_snapshot", 0) == 0
        if sample["operation"] == "read":
            assert counts["content_fingerprints.py:typed_snapshot"] == 1
        if sample.get("cache") == "intermediate":
            assert "source" not in sample["handlers"]
            assert "step" not in sample["handlers"]
            assert "root" in sample["handlers"]


def test_restoration_hashes_once_and_keeps_exports_independent(tmp_path: Path) -> None:
    raw = payload()
    value = stored_locations(raw)
    cache = TableCache(tmp_path)
    with cache.lock("value"):
        cache.write_locations("value", value)
    with patch.object(
        content_fingerprints,
        "typed_snapshot",
        wraps=content_fingerprints.typed_snapshot,
    ) as compute:
        restored = cache.read_locations("value")
        assert compute.call_count == 1
        artifact = read_location_artifact(tmp_path / "value" / "locations.arrow")
        assert compute.call_count == 2
        inspected = artifact.to_locations()
        assert compute.call_count == 2
    assert restored is not None
    for result in [restored, inspected]:
        exported = result.to_dict()
        exported["features"][0]["geometry"]["coordinates"][0] = 99.0
        result.to_features()[0].properties["changed"] = True
        assert_exact(result, raw)
    assert_exact(value, raw)
    assert_exact(artifact.preview(1)[0]["geometry"]["coordinates"][0], 1.0)


def test_internal_wrapper_rejects_arbitrary_models() -> None:
    with pytest.raises(TypeError, match="LocationSnapshot"):
        locations_from_snapshot(cast("LocationSnapshot", stored_locations(payload())))
