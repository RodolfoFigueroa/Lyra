"""Minimal typed output for tests of graph and execution behavior."""

import pandas as pd
import pandera.pandas as pa


def value_schema() -> pa.DataFrameSchema:
    """Declare one integer column for sample rows."""
    return pa.DataFrameSchema(
        {"value": pa.Column(int, description="Sample value", metadata={"unit": None})},
        strict=True,
    )


def value_frame(rows: int = 0) -> pd.DataFrame:
    """Return the requested number of typed sample rows."""
    return pd.DataFrame({"value": pd.Series(range(rows), dtype="int64")})
