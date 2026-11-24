"""Construct real typed acceptance for result and transport fixtures."""

from datetime import UTC, datetime

import pandas as pd
from pydantic import Field, JsonValue, create_model

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.jobs import AcceptedJob, JobRunProvenance
from lyra.contracts.plugins import PluginInfo
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.table_fixtures import value_frame, value_schema


def result_provenance(data: dict) -> JobRunProvenance:
    """Accept a parameter-only fixture with the requested output declarations."""
    parameters = data.get("input", {})
    fields: dict = {name: (JsonValue, Field(description=name)) for name in parameters}
    model = create_model(
        "ResultParameters",
        __base__=MetricParameters,
        **fields,
    )

    def calculate(parameters: MetricParameters) -> pd.DataFrame:
        del parameters
        return value_frame()

    calculate.__annotations__["parameters"] = model
    decorated = metric(
        name=data["metric"],
        code_revision="1",
        description="Result fixture",
        outputs={key: value_schema() for key in data["outputs"]},
    )(calculate)
    plan = prepare_execution(
        compile_live_catalog({"fixture": PluginDefinition(metrics=[decorated])}),
        {
            "metric": data["metric"],
            "parameters": parameters,
        },
    )
    return JobRunProvenance(
        accepted=AcceptedJob(
            plan=WorkerRequest.from_plan(plan),
            catalog_fingerprint=data.get("catalog_fingerprint", "fixture"),
            plugin=PluginInfo.model_validate(data["plugin"]),
            created_at=data.get("created_at", datetime(2026, 1, 1, tzinfo=UTC)),
        )
    )
