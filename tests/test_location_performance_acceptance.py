"""Deterministic acceptance checks; timing thresholds belong outside tests."""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from lyra.data.artifact_reader import read_location_artifact
from lyra.data.locations import locations_from_snapshot
from lyra.runtime.cache import TableCache
from test_support.location_data import assert_exact, payload
from test_support.location_helpers import stored_locations
from tests.cache_helpers import cached_value
from tests.location_checks import check_boundaries

if TYPE_CHECKING:
    from lyra.data.location_snapshot import LocationSnapshot


def test_integrated_boundaries_and_graph_cache_states() -> None:
    samples = check_boundaries(7, 1)
    assert {sample["operation"] for sample in samples if "cache" not in sample} == {
        "construction",
        "validation",
        "fingerprint_compute",
        "fingerprint_reuse",
        "invocation",
        "features",
        "to_frame",
        "from_frame",
        "artifact_encode",
        "artifact_decode",
        "write",
        "read",
    }
    # Keep every graph shape and cache state exercised by the shared assertions.
    assert {
        (sample["operation"], sample["cache"])
        for sample in samples
        if "cache" in sample
    } == {
        (shape, mode)
        for shape in ("chain", "fanout", "shared")
        for mode in ("off", "empty", "populated", "intermediate", "revision", "corrupt")
    }
    for sample in samples:
        if sample.get("cache") == "populated":
            assert sample["handlers"] == ["provider"]
        if sample.get("cache") == "intermediate":
            assert "source" not in sample["handlers"]
            assert "step" not in sample["handlers"]
            assert "root" in sample["handlers"]


def test_restoration_preserves_contents_and_keeps_exports_independent(
    tmp_path: Path,
) -> None:
    raw = payload()
    value = stored_locations(raw)
    cache = TableCache(tmp_path)
    with cache.lock("value"):
        cache.write_locations("value", value)
    restored = cached_value(cache.read_locations("value"))
    artifact = read_location_artifact(
        cache.store.directory / str(cache.reference("value")) / "locations.arrow"
    )
    inspected = artifact.to_locations()
    assert restored is not None
    for result in [restored, inspected]:
        assert_exact(result, raw)
        independent = result.to_dict()
        exported = result.to_dict()
        exported["features"][0]["geometry"]["coordinates"][0] = 99.0
        result.to_features()[0].properties["changed"] = True
        assert_exact(result, raw)
        assert_exact(independent, raw)
        assert_exact(restored, raw)
        assert_exact(inspected, raw)
    assert_exact(value, raw)
    assert_exact(artifact.preview(1)[0]["geometry"]["coordinates"][0], 1.0)


def test_internal_wrapper_rejects_arbitrary_models() -> None:
    with pytest.raises(TypeError, match="LocationSnapshot"):
        locations_from_snapshot(cast("LocationSnapshot", stored_locations(payload())))
