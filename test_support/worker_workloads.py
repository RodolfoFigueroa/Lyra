"""Worker registry entries, accepted jobs and retained native results."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from lyra.contracts.jobs import (
    AcceptedJob,
    JobEnvelope,
    JobRunProvenance,
    JobStatusInfo,
    TableJobResult,
)
from lyra.contracts.outputs import OutputMetadata
from lyra.data.inspection import summarize_outputs
from lyra.data.results import serialize_outputs
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultDescriptions,
    ResultOutput,
)
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.storage_schema import describe
from lyra.server import worker
from lyra.server.job_store import JobObservation
from test_support.canonical_execution import CanonicalWorkload
from test_support.publication import publish_result
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


def result_workload(rows: int, store_path: Path) -> ResultWorkload:
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
        catalog=compile_live_catalog(
            {plugin.distribution: work.plugin}, build_revision="benchmark-build"
        ),
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

    result_ref = publish_fixture(work, rows, store_path, contract.outputs["root"])
    with SQLiteStore(store_path) as store:
        execution_id = (
            ResultDescriptions(store).read_complete(result_ref)[1].execution_id
        )
    result = TableJobResult.model_validate(
        {
            "job_id": "benchmark",
            "execution_id": execution_id,
            "result_ref": result_ref,
            "outputs": serialize_outputs("root", {"root": frame(rows)}),
            "output_metadata": contract.outputs,
            "provenance": provenance,
        }
    )
    observation = JobObservation(
        snapshot=JobStatusInfo(
            job_id="benchmark",
            metric="root",
            status="succeeded",
            execution_id=execution_id,
            result_ref=result_ref,
            created_at=provenance.accepted.created_at,
            updated_at=provenance.accepted.created_at,
            completed_at=provenance.accepted.created_at,
        ),
        result_ref=result_ref,
        execution_id=execution_id,
        inspection=summarize_outputs({"root": frame(rows)}),
        provenance=provenance,
    )
    return ResultWorkload(work, entry, native, envelope, result, observation)


def publish_fixture(
    work: Workload, rows: int, path: Path, metadata: OutputMetadata
) -> str:
    """Retain benchmark outputs in the production relational layout."""
    store = SQLiteStore(path)
    try:
        description = describe(
            work.plugin.implementation("root").schemas["root"], metadata=metadata
        )
        MetricTables(store).publish(
            (
                (
                    Header(
                        value_key="fixture",
                        node_id="root",
                        output_scope="unpartitioned",
                    ),
                    (),
                ),
            ),
            {"root": frame(rows)},
            {"root": description},
            location_inputs={
                Header(
                    value_key="fixture", node_id="root", output_scope="unpartitioned"
                ).value_key: InputBindings()
            },
        )
        registration = next(iter(store.layout.descriptions.values()))
        return publish_result(
            ResultDescriptions(store),
            ResultDescription(
                metric_name="root",
                output_names=("root",),
                parameters={},
                outputs={
                    "root": ResultOutput(
                        registration_key=registration.key,
                        owner_scope=None,
                        computations=(ComputationSelection(value_key="fixture"),),
                    )
                },
            ),
        )
    finally:
        store.close()
