"""Worker registry entries, accepted jobs and retained native results."""

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import Mock

from lyra.contracts.jobs import (
    AcceptedJob,
    JobEnvelope,
    JobRunProvenance,
    JobStatusInfo,
    TableJobResult,
)
from lyra.data.inspection import summarize_outputs
from lyra.data.results import serialize_outputs
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.server import worker
from lyra.server.job_store import JobObservation
from test_support.canonical_execution import CanonicalWorkload
from test_support.workloads import Workload, frame, installed, workload


def canonical_worker_entry(work: CanonicalWorkload) -> worker.RunnerMetricEntry:
    """Bind the accepted metric to the heavy queue with its captured catalog."""
    catalog = work.executor.catalog
    name = work.prepared.node
    return worker.RunnerMetricEntry(
        name,
        "heavy",
        catalog.graph.nodes[name].node,
        catalog.manifests["bench"],
        "bench",
        catalog,
        dict(work.executor.plugins),
    )


@dataclass
class ResultWorkload:
    work: Workload
    entry: worker.RunnerMetricEntry
    native: Mock
    envelope: dict
    result: TableJobResult
    observation: JobObservation


def result_workload(rows: int, artifacts: ArtifactStore) -> ResultWorkload:
    """Build an accepted job and publish its fixture output to the supplied store."""
    work = workload(rows)
    plugin = installed(work.plugin)[0]
    contract = next(node for node in plugin.manifest.nodes if node.name == "root")
    if contract.kind != "metric":
        message = "Benchmark requires a metric"
        raise ValueError(message)
    entry = worker.RunnerMetricEntry(
        metric_name="root",
        queue="default",
        contract=contract,
        plugin=plugin.manifest,
        distribution=plugin.distribution,
        catalog=compile_catalog({plugin.distribution: plugin.manifest}),
        implementations={plugin.distribution: BoundPlugin(work.plugin)},
    )
    native = Mock(id="benchmark", origin="default")
    plan = prepare_execution(entry.catalog, {"metric": "root"})
    provenance = JobRunProvenance(
        accepted=AcceptedJob(
            plan=plan,
            catalog_fingerprint="fixture",
            plugin=plugin.manifest.plugin,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    native.meta = {}
    envelope = JobEnvelope(
        job_id="benchmark",
        accepted=provenance.accepted,
    ).model_dump(mode="json")

    result = TableJobResult.model_validate(
        {
            "job_id": "benchmark",
            "outputs": serialize_outputs("root", {"root": frame(rows)}),
            "output_metadata": contract.outputs,
            "provenance": provenance,
        }
    )
    artifact_id = artifacts.write(
        {"root": frame(rows)}, work.plugin.implementation("root").schemas
    )
    observation = JobObservation(
        snapshot=JobStatusInfo(
            job_id="benchmark",
            metric="root",
            status="succeeded",
            created_at=provenance.accepted.created_at,
            updated_at=provenance.accepted.created_at,
            completed_at=provenance.accepted.created_at,
        ),
        artifact_id=artifact_id,
        inspection=summarize_outputs({"root": frame(rows)}),
        provenance=provenance,
    )
    return ResultWorkload(work, entry, native, envelope, result, observation)
