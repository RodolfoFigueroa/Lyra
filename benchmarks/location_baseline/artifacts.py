"""Measure complete standalone artifact operations."""

import argparse
import json
import resource
import sys
import time
import tracemalloc

from lyra.sdk import geometry_buffers, location_artifacts, property_buffers
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import payload


def main() -> None:
    """Report latency, allocation peaks, size and process peak RSS.

    Raises:
        RuntimeError: If round-trip identity changes.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=256)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("rows must be positive")
    raw = payload(args.rows)
    features = raw.pop("features")
    snapshot = LocationSnapshot(
        geometry_buffers.encode(f["geometry"] for f in features),
        property_buffers.encode(
            (f["id"] for f in features),
            (f["properties"] for f in features),
            **raw,
        ),
    )
    tracemalloc.start()
    start = time.perf_counter()
    artifact = location_artifacts.encode(snapshot)
    encode_seconds = time.perf_counter() - start
    encode_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    tracemalloc.start()
    start = time.perf_counter()
    restored = location_artifacts.decode(artifact)
    decode_seconds = time.perf_counter() - start
    decode_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    if restored.fingerprint != snapshot.fingerprint:
        message = "Round-trip fingerprint mismatch"
        raise RuntimeError(message)
    sys.stdout.write(
        json.dumps(
            {
                "rows": len(features),
                "artifact_bytes": len(artifact),
                "encode_seconds": encode_seconds,
                "decode_seconds": decode_seconds,
                "encode_python_peak_bytes": encode_peak,
                "decode_python_peak_bytes": decode_peak,
                "process_peak_rss_kib": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
