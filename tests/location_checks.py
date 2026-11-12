"""Shared production regression fixtures and assertions."""

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, TypeVar, cast

import pandas as pd
import pandera.pandas as pa

from lyra.data import content_fingerprints, location_artifacts
from lyra.data.locations import Locations, location_fingerprint, location_snapshot
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.cache import TableCache
from lyra.sdk.location_schema import LocationOutputSchema
from test_support.location_data import assert_exact, payload
from test_support.location_helpers import fixture_locations, producer_result
from tests.cache_helpers import cached_value
from tests.location_graph import schema, workload

if TYPE_CHECKING:
    from lyra.data.location_snapshot import LocationSnapshot


def rich_payload(rows: int) -> dict:
    """Return schema-representable mixed geometry and typed property inputs."""
    raw = payload(rows)
    geometries = [f["geometry"] for f in payload(7)["features"]]
    for index, feature in enumerate(raw["features"]):
        feature["geometry"] = geometries[index % len(geometries)]
        feature["properties"] = {
            "label": f"place-{index}",
            "count": 2**60 + index,
            "ratio": index / 8,
            "active": bool(index % 2),
        }
    return raw


T = TypeVar("T")


class Recorder:
    """Record exercised scenarios for acceptance coverage checks."""

    def __init__(self) -> None:
        """Start an independent scenario record."""
        self.records: list[dict] = []

    def record(self, action: Callable[[], T], **labels: object) -> T:
        """Run an action and record its scenario.

        Returns:
            The action's unmodified result for subsequent correctness checks.
        """
        result = action()
        self.records.append(labels)
        return result


def rich_schema() -> LocationOutputSchema:
    """Return the typed property contract for the rich workload."""
    output = schema()
    for name, dtype in {
        "label": str,
        "count": int,
        "ratio": float,
        "active": bool,
    }.items():
        output.schema = output.schema.add_columns(
            {name: pa.Column(dtype, description=name, metadata={"unit": None})}
        )
    # Recompile metadata as well as the schema used by the graph.
    return type(output)(output.schema)


def write_bundle(cache: TableCache, value: Locations) -> None:
    """Publish a complete location bundle under its invocation lock."""
    with cache.lock("value"):
        cache.write_locations("value", value)


def boundaries(
    recorder: Recorder, value: Locations, cache: TableCache, repeat: int
) -> None:
    """Exercise boundaries and verify exact contents and identities."""
    raw = value.to_dict()
    output = rich_schema()
    snapshot = location_snapshot(value)
    frame = producer_result(value, output)
    artifact = location_artifacts.encode(snapshot)
    expected_identity = PreparedResolvedInvocation(
        "root", None, {"location": value}
    ).identity
    actions = {
        "construction": lambda: fixture_locations(raw, schema=output).with_binding(
            None, None
        ),
        "validation": lambda: output.validate(value),
        "fingerprint_compute": lambda: content_fingerprints.typed_snapshot(
            snapshot.table,
            snapshot.geometry,
            (
                snapshot.scope.collection,
                snapshot.scope.unit_type,
                snapshot.scope.crs,
                snapshot.scope.id_column,
                snapshot.scope.geometry_column,
                *snapshot.scope.columns,
                json.dumps(
                    None
                    if snapshot.scope.contract is None
                    else snapshot.scope.contract.model_dump(),
                    sort_keys=True,
                ),
            ),
        ),
        "fingerprint_reuse": lambda: location_fingerprint(value),
        "invocation": lambda: (
            PreparedResolvedInvocation("root", None, {"location": value}).identity
        ),
        "features": value.to_dict,
        "to_frame": value.to_geodataframe,
        "from_frame": lambda: output.build(
            frame, collection=value.collection, unit_type=value.unit_type
        ),
        "artifact_encode": lambda: location_artifacts.encode(snapshot),
        "artifact_decode": lambda: location_artifacts.decode(artifact),
        "write": lambda: write_bundle(cache, value),
        "read": lambda: cached_value(cache.read_locations("value")),
    }
    for name, action in actions.items():
        result = recorder.record(action, operation=name, repeat=repeat)
        if name == "fingerprint_compute":
            assert_exact(
                result,
                (
                    snapshot.geometry_fingerprint,
                    snapshot.non_geometry_fingerprint,
                    snapshot.fingerprint,
                ),
            )
        elif name == "fingerprint_reuse":
            assert_exact(result, snapshot.fingerprint)
        elif name == "invocation":
            assert_exact(result, expected_identity)
        elif name == "to_frame":
            pd.testing.assert_frame_equal(
                cast("pd.DataFrame", result), value.to_geodataframe(), check_exact=True
            )
        elif name == "artifact_encode":
            assert_exact(
                location_artifacts.decode(cast("bytes", result)).to_dict(), raw
            )
        elif name == "artifact_decode":
            assert_exact(cast("LocationSnapshot", result).to_dict(), raw)
        elif name == "write":
            assert_exact(cached_value(cache.read_locations("value")), raw)
        else:
            assert_exact(result, raw)


def invalidate(directory: Path, mode: str) -> None:
    """Remove metric pointers and optionally corrupt location artifacts."""
    if mode not in {"intermediate", "corrupt"}:
        return
    for pointer in (directory / "cache").iterdir():
        if not pointer.is_file() or pointer.name.endswith(".lock"):
            continue
        bundle = directory / "bundles" / pointer.read_text()
        if json.loads((bundle / "manifest.json").read_text())["kind"] == "metric":
            pointer.unlink()
        elif mode == "corrupt":
            (bundle / "locations.arrow").write_bytes(b"invalid")


def graphs(
    recorder: Recorder, value: Locations, directory: Path, shape: str, repeat: int
) -> None:
    """Verify graph results, intermediate restoration, and invalidation."""
    output = rich_schema()
    for mode in ("off", "empty", "populated", "intermediate", "revision", "corrupt"):
        invalidate(directory, mode)
        runner, calls = workload(
            value,
            shape,
            None if mode == "off" else directory,
            output=output,
        )
        result = recorder.record(
            partial(
                runner.run,
                "root",
                {},
                cache_revision="changed" if mode == "revision" else None,
            ),
            operation=shape,
            cache=mode,
            repeat=repeat,
        )
        pd.testing.assert_frame_equal(
            result["root"], pd.DataFrame({"count": [2 * len(value.ids)]})
        )
        if mode == "populated":
            assert_exact(calls, [])
        elif mode == "intermediate":
            assert_exact(
                calls,
                ["left", "root"] if shape == "chain" else ["left", "right", "root"],
            )
        else:
            assert_exact(calls.count("source"), 1)
            assert_exact(calls.count("provider"), 1)
        recorder.records[-1]["handlers"] = calls


def check_boundaries(rows: int, repeats: int) -> list[dict]:
    """Exercise production boundaries and return the scenarios checked."""
    recorder = Recorder()
    raw = rich_payload(rows)
    value = fixture_locations(raw, schema=rich_schema()).with_binding(None, None)
    assert_exact(value, raw)
    with TemporaryDirectory() as scratch:
        cache = TableCache(Path(scratch) / "boundary")
        cache.directory.mkdir(parents=True)
        for repeat in range(repeats):
            boundaries(recorder, value, cache, repeat)
            for shape in ("chain", "fanout", "shared"):
                graphs(
                    recorder, value, Path(scratch) / f"{shape}-{repeat}", shape, repeat
                )
    return recorder.records
