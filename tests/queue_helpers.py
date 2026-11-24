"""Accept real bound plans for worker tests with in-process registries."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.jobs import AcceptedJob, JobEnvelope, TableJobResult
from lyra.data.results import serialize_outputs
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.result_store import ResultStore
from lyra.runtime.results import RunResult
from lyra.server import worker


def accepted_envelope(
    *,
    job_id: str,
    metric: str,
    inputs: Mapping[str, object],
    catalog: CompiledCatalog | None = None,
) -> JobEnvelope:
    """Capture a real request against supplied or installed worker contracts."""
    if catalog is None:
        catalog = worker.require_worker_runtime().catalog.compiled
    plan = prepare_execution(
        catalog,
        {
            "metric": metric,
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
            plan=WorkerRequest.from_plan(plan),
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
        values["parameters"] = request.parameters
    return values


def run_and_read(task: Callable[..., dict], payload: dict, *args: object) -> dict:
    """Execute a worker entry point and read its persisted native result."""
    reference = task(payload, *args)
    assert set(reference) == {"job_id"}
    envelope = JobEnvelope.model_validate(payload)
    retained = read_retained(reference["job_id"])
    return TableJobResult.model_validate(
        {
            "job_id": envelope.job_id,
            "outputs": serialize_outputs(
                envelope.accepted.plan.metric, retained.tables
            ),
            "output_metadata": {
                name: output.table_schema
                for name, output in retained.metadata.outputs.items()
            },
        }
    ).model_dump(mode="json", exclude={"provenance"})


def read_retained(reference: str) -> RunResult:
    """Read worker output through the test-owned store acquisition boundary."""
    with ResultStore(
        worker.SQLiteStore(worker.require_worker_config().result_store_path)
    ) as store:
        return store.read_result(reference)
