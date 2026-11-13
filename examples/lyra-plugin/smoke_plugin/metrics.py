"""Metric functions used by the example smoke-test plugin."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.contracts.units import Unit
from lyra.sdk import (
    LocationDependency,
    MetricParameters,
    RunContext,
    metric,
)
from smoke_plugin.locations import SUBJECT


# docs:start table-inputs
class Parameters(MetricParameters):
    """Parameters shared by the table and progress examples."""

    value: int = Field(description="Value copied into each output row.")


# docs:end table-inputs


# docs:start table-output
def _value_output() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "value": pa.Column(
                "int64", description="Submitted value.", metadata={"unit": Unit.COUNT}
            )
        },
        strict=True,
    )


# docs:end table-output


# docs:start table-metric
@metric(
    inputs={"location": LocationDependency(provider=SUBJECT)},
    code_revision="1",
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    outputs={"smoke_table_metric": _value_output()},
)
def run_table(
    location: gpd.GeoDataFrame,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Copy the submitted integer into a row for every input feature.

    Returns:
        A typed table in input feature order.
    """
    context.report_progress(stage="table", current=1, total=1)
    return pd.DataFrame(
        {"value": pd.Series([parameters.value] * len(location), dtype="int64")},
    )


# docs:end table-metric


@metric(
    inputs={"location": LocationDependency(provider=SUBJECT)},
    code_revision="1",
    name="smoke_summary_metric",
    description="Return a summary for the submitted features.",
    outputs={"smoke_summary_metric": _value_output()},
)
def run_summary(location: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return one summary value for each feature."""
    return pd.DataFrame({"value": pd.Series([1] * len(location), dtype="int64")})


@metric(
    inputs={"location": LocationDependency(provider=SUBJECT)},
    code_revision="1",
    name="smoke_progress_metric",
    description="Emit progress before returning.",
    outputs={"smoke_progress_metric": _value_output()},
)
def run_progress(
    location: gpd.GeoDataFrame,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Exercise progress reporting before producing a table result.

    Returns:
        A table result after reporting progress.
    """
    context.report_progress(stage="complete", current=1, total=1)
    return pd.DataFrame(
        {"value": pd.Series([parameters.value] * len(location), dtype="int64")},
    )
