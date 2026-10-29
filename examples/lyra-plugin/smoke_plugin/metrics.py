"""Metric functions used by the example smoke-test plugin."""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from lyra.sdk import LocationInput, MetricParameters, RunContext, Unit, metric
from pydantic import Field


# docs:start table-inputs
class Parameters(MetricParameters):
    """Parameters shared by the table and progress examples."""

    value: int = Field(description="Value copied into each output row.")


def _feature_ids(location: LocationInput) -> list[str]:
    return [feature.id for feature in location.features]


# docs:end table-inputs


# docs:start table-output
def _value_output() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "value": pa.Column(
                "int64", description="Submitted value.", metadata={"unit": Unit.COUNT}
            )
        }
    )


# docs:end table-output


# docs:start table-metric
@metric(
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    outputs={"smoke_table_metric": _value_output()},
)
def run_table(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Copy the submitted integer into a row for every input feature.

    Returns:
        A table result indexed by the input feature identifiers.
    """
    context.report_progress(stage="table", current=1, total=1)
    feature_ids = _feature_ids(location)
    return pd.DataFrame(
        {"value": [parameters.value for _feature_id in feature_ids]},
        index=feature_ids,
    )


# docs:end table-metric


@metric(
    name="smoke_summary_metric",
    description="Return a summary for the submitted features.",
    outputs={"smoke_summary_metric": _value_output()},
)
def run_summary(location: LocationInput) -> pd.DataFrame:
    """Return one summary value for each feature."""
    return pd.DataFrame(
        {"value": [1] * len(location.features)}, index=_feature_ids(location)
    )


@metric(
    name="smoke_progress_metric",
    description="Emit progress before returning.",
    outputs={"smoke_progress_metric": _value_output()},
)
def run_progress(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Exercise progress reporting before producing a table result.

    Returns:
        A table result after reporting progress.
    """
    context.report_progress(stage="complete", current=1, total=1)
    feature_ids = _feature_ids(location)
    return pd.DataFrame(
        {"value": [parameters.value for _feature_id in feature_ids]},
        index=feature_ids,
    )
