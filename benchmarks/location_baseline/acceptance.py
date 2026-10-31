"""Integrated acceptance measurements, with separate untimed call-count passes."""

import argparse
import json
import platform
import sys
from collections import Counter
from collections.abc import Callable
from functools import partial
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import FrameType
from typing import TYPE_CHECKING, ClassVar, TypeVar, cast

import pandas as pd
import pandera.pandas as pa
from lyra.sdk import (
    LocationOutputSchema,
    Locations,
    content_fingerprints,
    location_artifacts,
)
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.cache import TableCache
from lyra.sdk.models.location import location_fingerprint, location_snapshot

from benchmarks.location_baseline.fixtures import assert_exact, payload
from benchmarks.location_baseline.run import schema, workload
from benchmarks.metric_representation.execution import peak_mib

if TYPE_CHECKING:
    from lyra.sdk.location_snapshot import LocationSnapshot

T = TypeVar("T")


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


class Recorder:
    """Collect timings or diagnostic counts without timing comparison checks."""

    def __init__(self, *, diagnostic: bool) -> None:
        """Start an independent recorder."""
        self.records: list[dict] = []
        self.counts: Counter[str] = Counter()
        self.diagnostic = diagnostic

    WATCHED: ClassVar = {
        ("location_schema.py", "validate"),
        ("location.py", "validated_locations"),
        ("content_fingerprints.py", "compute"),
        ("geometry_buffers.py", "decode"),
        ("property_buffers.py", "decode"),
        ("execution.py", "_prepare"),
        ("arguments.py", "prepare_dependency"),
        ("plugin.py", "prepare_invocation"),
    }

    def _profile(self, frame: FrameType, event: str, _arg: object) -> None:
        if event != "call":
            return
        key = (frame.f_code.co_filename.rsplit("/", 1)[-1], frame.f_code.co_name)
        if key in self.WATCHED:
            self.counts[":".join(key)] += 1

    def sample(self, action: Callable[[], T], **labels: object) -> T:
        """Run an action and record elapsed time and cumulative process memory.

        Returns:
            The action's unmodified result for subsequent correctness checks.
        """
        self.counts.clear()
        previous = sys.getprofile()
        if self.diagnostic:
            sys.setprofile(self._profile)
        try:
            start = perf_counter()
            result = action()
            seconds = perf_counter() - start
        finally:
            if self.diagnostic:
                sys.setprofile(previous)
        self.records.append(
            labels
            | {"seconds": seconds, "peak_mib": peak_mib(), "counts": dict(self.counts)}
        )
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
    """Measure boundaries and verify exact restored contents outside timings."""
    raw = value.to_dict()
    output = rich_schema()
    snapshot = location_snapshot(value)
    frame = value.to_geodataframe()
    artifact = location_artifacts.encode(snapshot)
    expected_identity = PreparedResolvedInvocation(
        "root", None, {"location": value}
    ).identity
    actions = {
        "construction": lambda: Locations.from_dict(raw),
        "validation": lambda: output.validate(value),
        "fingerprint_compute": lambda: content_fingerprints.compute(
            snapshot.geometry, snapshot.properties
        ),
        "fingerprint_reuse": lambda: location_fingerprint(value),
        "invocation": lambda: (
            PreparedResolvedInvocation("root", None, {"location": value}).identity
        ),
        "features": value.to_dict,
        "to_frame": value.to_geodataframe,
        "from_frame": lambda: Locations.from_geodataframe(
            frame,
            collection=value.collection,
            unit_type=value.unit_type,
            id_column="id",
        ),
        "artifact_encode": lambda: location_artifacts.encode(snapshot),
        "artifact_decode": lambda: location_artifacts.decode(artifact),
        "write": lambda: write_bundle(cache, value),
        "read": lambda: cache.read_locations("value"),
    }
    for name, action in actions.items():
        result = recorder.sample(action, operation=name, repeat=repeat)
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
                cast("pd.DataFrame", result), frame, check_exact=True
            )
        elif name == "artifact_encode":
            assert_exact(
                location_artifacts.decode(cast("bytes", result)).to_dict(), raw
            )
        elif name == "artifact_decode":
            assert_exact(cast("LocationSnapshot", result).to_dict(), raw)
        elif name == "write":
            assert_exact(cache.read_locations("value"), raw)
        else:
            assert_exact(result, raw)
        recorder.records[-1]["artifact_bytes"] = len(artifact)


def invalidate(directory: Path, mode: str) -> None:
    """Remove metric pointers and optionally corrupt location artifacts."""
    if mode not in {"intermediate", "corrupt"}:
        return
    for pointer in directory.iterdir():
        if not pointer.is_symlink():
            continue
        if json.loads((pointer / "manifest.json").read_text())["kind"] == "metric":
            pointer.unlink()
        elif mode == "corrupt":
            (pointer / "locations.arrow").write_bytes(b"invalid")


def graphs(
    recorder: Recorder, value: Locations, directory: Path, shape: str, repeat: int
) -> None:
    """Measure graph modes including intermediate restoration and invalidation."""
    output = rich_schema()
    for mode in ("off", "empty", "populated", "intermediate", "revision", "corrupt"):
        invalidate(directory, mode)
        runner, calls = workload(
            value,
            shape,
            None if mode == "off" else directory,
            output=output,
        )
        result = recorder.sample(
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
        recorder.records[-1].update(
            handlers=calls,
            artifact_bytes=sum(
                p.stat().st_size for p in directory.rglob("*") if p.is_file()
            ),
        )


def measure(rows: int, repeats: int, *, diagnostic: bool = False) -> list[dict]:
    """Return verified samples; diagnostic timings are for counts only."""
    recorder = Recorder(diagnostic=diagnostic)
    raw = rich_payload(rows)
    value = Locations.from_dict(raw)
    assert_exact(value, raw)
    with TemporaryDirectory() as scratch:
        cache = TableCache(Path(scratch) / "boundary")
        cache.directory.mkdir()
        for repeat in range(repeats):
            boundaries(recorder, value, cache, repeat)
            for shape in ("chain", "fanout", "shared"):
                graphs(
                    recorder, value, Path(scratch) / f"{shape}-{repeat}", shape, repeat
                )
    return recorder.records


def main() -> None:
    """Write timings and independent diagnostic counts for one process size."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    samples = measure(args.rows, args.repeats)
    diagnostics = measure(args.rows, 1, diagnostic=True)
    for sample in samples:
        matching = next(
            d
            for d in diagnostics
            if (d["operation"], d.get("cache"))
            == (sample["operation"], sample.get("cache"))
        )
        sample["counts"] = matching["counts"]
    args.output.write_text(
        json.dumps(
            {
                "rows": args.rows,
                "repeats": args.repeats,
                "python": platform.python_version(),
                "versions": {
                    name: version(name)
                    for name in (
                        "pandas",
                        "geopandas",
                        "shapely",
                        "pydantic",
                        "pandera",
                        "pyarrow",
                    )
                },
                "samples": samples,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
