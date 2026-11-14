"""Native metric publication fixtures for disposable stores."""

from pathlib import Path

import pandas as pd

from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.storage_schema import prepare_native
from test_support.workloads import schema


def write_outputs(
    path: Path, outputs: dict[str, pd.DataFrame], key: str = "fixture"
) -> None:
    """Validate and publish named tables under a caller-selected identity."""
    prepared = {
        name: prepare_native(frame, schema(frame)) for name, frame in outputs.items()
    }
    with SQLiteStore(path / "values.sqlite") as store:
        MetricTables(store).publish(
            Header(value_key=key, node_id="fixture", output_scope="unpartitioned"),
            {name: pair[0] for name, pair in prepared.items()},
            {name: pair[1] for name, pair in prepared.items()},
        )


def read_outputs(path: Path, key: str = "fixture") -> dict[str, pd.DataFrame]:
    """Read detached tables using retained relational descriptions."""
    with SQLiteStore(path / "values.sqlite") as store:
        result = MetricTables(store).lookup(
            Header(value_key=key, node_id="fixture", output_scope="unpartitioned"),
            required=True,
        )
        assert result is not None
        return result
