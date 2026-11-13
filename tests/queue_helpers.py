"""Accept real bound plans for worker tests with in-process registries."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime

from lyra.contracts.jobs import AcceptedJob, JobEnvelope, TableJobResult
from lyra.data.results import serialize_outputs
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.server import worker


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
        catalog = entry.catalog
        catalog = replace(
            catalog, build_revision=worker.require_worker_config().build_revision
        )
    plan = prepare_execution(
        catalog,
        {
            "metric": metric,
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
    if envelope.accepted.plan.has_parameters:
        values["parameters"] = envelope.accepted.plan.parameters
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
