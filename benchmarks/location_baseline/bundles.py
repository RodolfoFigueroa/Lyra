"""Measure complete filesystem bundle writes and validated reads."""

import argparse
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pandera.pandas as pa
from lyra.sdk.cache import TableCache

from benchmarks.location_baseline.fixtures import payload
from tests.location_helpers import stored_locations


def main() -> None:
    """Measure both result kinds including manifests and publication.

    Raises:
        RuntimeError: If a published bundle cannot be restored.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=256)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("rows must be positive")
    rows = args.rows
    locations = stored_locations(payload(rows))
    table = {"table": pd.DataFrame({"value": range(rows)})}
    results = {}
    with TemporaryDirectory() as scratch:
        cache = TableCache(Path(scratch))
        for kind in ("locations", "tables"):
            start = time.perf_counter()
            with cache.lock(kind):
                if kind == "locations":
                    cache.write_locations(kind, locations)
                else:
                    cache.write(
                        kind,
                        table,
                        {"table": pa.DataFrameSchema({"value": pa.Column(int)})},
                    )
            write_seconds = time.perf_counter() - start
            start = time.perf_counter()
            restored = (
                cache.read_locations(kind) if kind == "locations" else cache.read(kind)
            )
            read_seconds = time.perf_counter() - start
            if restored is None:
                message = "Bundle did not restore"
                raise RuntimeError(message)
            results[kind] = {
                "rows": rows,
                "write_seconds": write_seconds,
                "read_seconds": read_seconds,
                "bytes": sum(
                    p.stat().st_size for p in (Path(scratch) / kind).iterdir()
                ),
            }
    sys.stdout.write(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
