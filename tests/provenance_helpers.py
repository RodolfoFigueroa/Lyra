"""Construct real typed acceptance for result and transport fixtures."""

from datetime import UTC, datetime

from pydantic import Field, JsonValue, create_model

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.jobs import AcceptedJob, JobRunProvenance
from lyra.contracts.plugins import MetricManifest, PluginManifest
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters, build_parameter_schema
from test_support.manifest_catalog import compile_catalog
from test_support.parameter_catalog import with_models


def result_provenance(data: dict) -> JobRunProvenance:
    """Accept a parameter-only fixture with the requested output declarations."""
    parameters = data.get("input", {})
    fields: dict = {name: (JsonValue, Field(description=name)) for name in parameters}
    model = create_model(
        "ResultParameters",
        __base__=MetricParameters,
        **fields,
    )
    node = MetricManifest(
        code_revision="1",
        name=data["metric"],
        description="Result fixture",
        parameters_schema=build_parameter_schema(data["metric"], model),
        outputs=data["outputs"],
    )
    manifest = PluginManifest(
        plugin=data["plugin"], factory="fixture:create", nodes=[node]
    )
    plan = prepare_execution(
        with_models(compile_catalog({"fixture": manifest}), {data["metric"]: model}),
        {
            "metric": data["metric"],
            "parameters": parameters,
        },
    )
    return JobRunProvenance(
        accepted=AcceptedJob(
            plan=WorkerRequest.from_plan(plan),
            catalog_fingerprint=data.get("catalog_fingerprint", "fixture"),
            plugin=manifest.plugin,
            created_at=data.get("created_at", datetime(2026, 1, 1, tzinfo=UTC)),
        )
    )
