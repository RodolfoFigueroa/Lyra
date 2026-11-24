"""Worker catalogs, accepted jobs and retained native results."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from lyra.contracts.accepted import WorkerRequest
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
from lyra.server.job_store import JobObservation
from lyra.server.registry import Catalog
from test_support.canonical_execution import CanonicalWorkload
from test_support.publication import publish_result
from test_support.workloads import Workload, frame, installed, workload


def canonical_worker_catalog(work: CanonicalWorkload) -> Catalog:
    """Capture the benchmark catalog at its deployment revision."""
    compiled = replace(work.executor.catalog, build_revision="benchmark-build")
    return Catalog(compiled)


@dataclass
class ResultWorkload:
    work: Workload
    catalog: Catalog
    native: Mock
    envelope: dict
    result: TableJobResult
    observation: JobObservation


def result_workload(rows: int, store_path: Path) -> ResultWorkload:
    """Build an accepted job and publish its fixture output to the supplied store."""
    work = workload(rows)
    plugin = installed(work.plugin)[0]
    catalog = Catalog(
        compile_live_catalog(
            {plugin.distribution: work.plugin}, build_revision="benchmark-build"
        )
    )
    contract = catalog.compiled.graph.nodes["root"].node
    if contract.kind != "metric":
        message = "Benchmark requires a metric"
        raise ValueError(message)
    job_id = publish_fixture(work, rows, store_path, contract.outputs["root"])
    native = Mock(id=job_id)
    plan = prepare_execution(catalog.compiled, {"metric": "root"})
    provenance = JobRunProvenance(
        accepted=AcceptedJob(
            plan=WorkerRequest.from_plan(plan),
            catalog_fingerprint="fixture",
            plugin=catalog.compiled.manifests[plugin.distribution].plugin,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    native.meta = {}
    envelope = JobEnvelope(
        job_id=job_id,
        accepted=provenance.accepted,
    ).model_dump(mode="json")

    result = TableJobResult.model_validate(
        {
            "job_id": job_id,
            "outputs": serialize_outputs("root", {"root": frame(rows)}),
            "output_metadata": contract.outputs,
            "provenance": provenance,
        }
    )
    observation = JobObservation(
        snapshot=JobStatusInfo(
            job_id=job_id,
            metric="root",
            status="succeeded",
            created_at=provenance.accepted.created_at,
            updated_at=provenance.accepted.created_at,
            completed_at=provenance.accepted.created_at,
        ),
        inspection=summarize_outputs({"root": frame(rows)}),
        provenance=provenance,
    )
    return ResultWorkload(work, catalog, native, envelope, result, observation)


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
