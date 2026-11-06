"""Accept real bound plans for worker tests with in-process registries."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from lyra.sdk.artifacts import ArtifactStore
from lyra.sdk.catalog import CompiledCatalog, compile_catalog
from lyra.sdk.models.job import AcceptedJob, JobEnvelope, TableJobResult
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from lyra.sdk.preparation import prepare_execution
from lyra.sdk.resources import resource_metadata
from lyra.sdk.results import serialize_outputs

from lyra_app import worker


def accepted_envelope(
    *,
    job_id: str,
    metric: str,
    inputs: Mapping[str, object],
    cache_revision: str | None = None,
    catalog: CompiledCatalog | None = None,
) -> JobEnvelope:
    """Capture a real request against supplied or installed worker contracts."""
    if catalog is None:
        entry = worker.RUNNER_REGISTRY[metric]
        distributions = {node.distribution for node in entry.graph.nodes.values()}
        if entry.location_provider:
            distributions.add(entry.location_provider.distribution)
        catalog = compile_catalog(
            {
                distribution: PluginManifest(
                    plugin=PluginInfo(name=distribution, version="test"),
                    factory="test:create",
                    nodes=[
                        node.node
                        for node in entry.graph.nodes.values()
                        if node.distribution == distribution
                    ],
                    resources={
                        name: resource_metadata(provider)
                        for name, provider in entry.implementations[
                            distribution
                        ].resources.items()
                    }
                    if distribution in entry.implementations
                    else {},
                    location_provider=entry.location_provider.declaration
                    if entry.location_provider
                    and entry.location_provider.distribution == distribution
                    else None,
                    route_preferences=entry.implementations[
                        distribution
                    ].route_preferences
                    if distribution in entry.implementations
                    else (),
                )
                for distribution in distributions
            }
        )
    plan = prepare_execution(
        catalog,
        {
            "metric": metric,
            "edition": None,
            "cache_revision": cache_revision,
            **({"parameters": inputs["parameters"]} if "parameters" in inputs else {}),
            "inputs": {
                key: value for key, value in inputs.items() if key != "parameters"
            },
        },
    )
    entry = catalog.graph.nodes[plan.node]
    return JobEnvelope(
        job_id=job_id,
        accepted=AcceptedJob(
            plan=plan,
            catalog_fingerprint="test-catalog",
            plugin=catalog.manifests[entry.distribution].plugin,
            created_at=datetime.now(UTC),
        ),
    )


def submitted_input(envelope: JobEnvelope) -> dict:
    request = envelope.accepted.plan.request
    values = {
        name: item.model_dump(mode="json") for name, item in request.inputs.items()
    }
    if "parameters" in request.supplied_fields:
        values["parameters"] = request.parameters
    return values


def run_and_read(task: Callable[..., dict], payload: dict, *args: object) -> dict:
    """Execute a worker entry point and read its persisted native result."""
    reference = task(payload, *args)
    assert set(reference) == {"artifact_id"}
    envelope = JobEnvelope.model_validate(payload)
    outputs = ArtifactStore(worker.require_worker_config().artifact_dir).read(
        reference["artifact_id"]
    )
    return TableJobResult.model_validate(
        {
            "job_id": envelope.job_id,
            "outputs": serialize_outputs(envelope.accepted.plan.node, outputs),
            "output_metadata": envelope.accepted.plan.output_metadata,
        }
    ).model_dump(mode="json", exclude={"provenance"})
