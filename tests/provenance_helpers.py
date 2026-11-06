"""Construct real typed acceptance for result and transport fixtures."""

from datetime import UTC, datetime

from lyra.contracts.jobs import AcceptedJob, JobRunProvenance
from lyra.contracts.plugins import MetricManifest, PluginManifest
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.preparation import prepare_execution


def result_provenance(data: dict) -> JobRunProvenance:
    """Accept a parameter-only fixture with the requested output declarations."""
    parameters = data.get("input", {})
    node = MetricManifest(
        name=data["metric"],
        description="Result fixture",
        family=data["metric"] if data.get("edition") else None,
        edition=data.get("edition"),
        parameters_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {name: {} for name in parameters},
        },
        outputs=data["outputs"],
    )
    manifest = PluginManifest(
        plugin=data["plugin"], factory="fixture:create", nodes=[node]
    )
    plan = prepare_execution(
        compile_catalog({"fixture": manifest}),
        {
            "metric": data["metric"],
            "edition": data.get("edition"),
            "cache_revision": data.get("cache_revision"),
            "parameters": parameters,
        },
    )
    return JobRunProvenance(
        accepted=AcceptedJob(
            plan=plan,
            catalog_fingerprint=data.get("catalog_fingerprint", "fixture"),
            plugin=manifest.plugin,
            created_at=data.get("created_at", datetime(2026, 1, 1, tzinfo=UTC)),
        )
    )
