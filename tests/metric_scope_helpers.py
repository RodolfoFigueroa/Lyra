"""Shared location-scoped metric schemas."""

import pandera.pandas as pa

from test_support.location_schema import column


def identity_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"location_id": column(str), "row_key": column(str)},
        strict=True,
        unique=["location_id", "row_key"],
    )
