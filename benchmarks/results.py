"""Worker execution and bounded public result projections."""

from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import Mock, patch

from lyra.sdk.catalog import compile_catalog
from lyra.sdk.graph import build_graph
from lyra.sdk.models.job import (
    AcceptedJob,
    JobEnvelope,
    JobRunProvenance,
    JobStatusInfo,
    TableJobResult,
)
from lyra.sdk.preparation import prepare_execution
from lyra.sdk.results import serialize_outputs

from benchmarks.workloads import frame, installed, workload
from lyra_app import worker
from lyra_app.config import LyraConfig
from lyra_app.job_store import JobObservation
from lyra_app.mcp.results import project_observation


class ResultSuite:
    """Execute queued contracts and inspect complete retained native results."""

    version = "3"
    params: ClassVar[list[int]] = [1000, 10000]

    def setup(self, rows: int) -> None:
        """Prepare a real worker entry with a native job double.

        Raises:
            ValueError: If the fixture is not a metric.
        """
        self.work = workload(rows)
        plugin = installed(self.work.plugin)[0]
        contract = next(node for node in plugin.manifest.nodes if node.name == "root")
        if contract.kind != "metric":
            message = "Benchmark requires a metric"
            raise ValueError(message)
        self.entry = worker.RunnerMetricEntry(
            metric_name="root",
            queue="default",
            contract=contract,
            plugin=plugin.manifest,
            distribution=plugin.distribution,
            graph=build_graph({plugin.distribution: plugin.manifest.nodes}),
            implementations={plugin.distribution: self.work.plugin},
        )
        self.native = Mock(id="benchmark", origin="default")
        plan = prepare_execution(
            compile_catalog({plugin.distribution: plugin.manifest}), {"metric": "root"}
        )
        self.provenance = JobRunProvenance(
            accepted=AcceptedJob(
                plan=plan,
                catalog_fingerprint="fixture",
                plugin=plugin.manifest.plugin,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        self.native.meta = {}
        self.envelope = JobEnvelope(
            job_id="benchmark",
            accepted=self.provenance.accepted,
        ).model_dump(mode="json")

        if contract.kind != "metric":
            message = "Benchmark requires a metric"
            raise ValueError(message)
        self.result = TableJobResult.model_validate(
            {
                "job_id": "benchmark",
                "outputs": serialize_outputs("root", {"root": frame(rows)}),
                "output_metadata": contract.outputs,
                "provenance": self.provenance,
            }
        )
        self.observation = JobObservation(
            snapshot=JobStatusInfo(
                job_id="benchmark",
                metric="root",
                status="succeeded",
                created_at=self.provenance.accepted.created_at,
                updated_at=self.provenance.accepted.created_at,
                completed_at=self.provenance.accepted.created_at,
            ),
            result=self.result,
            provenance=self.provenance,
        )
        self.registry_patch = patch.dict(
            worker.RUNNER_REGISTRY, {"root": self.entry}, clear=True
        )
        self.config_patch = patch.object(
            worker,
            "require_worker_config",
            return_value=Mock(
                spec=LyraConfig, jobs=Mock(), cache_dir=None, resources={}
            ),
        )
        self.registry_patch.start()
        self.config_patch.start()

    def time_worker(self, _rows: int) -> None:
        """Validate accepted job bindings, execute, and serialize native outputs."""
        worker.execute_job(self.envelope, self.native)

    def time_preview(self, _rows: int) -> None:
        """Build bounded MCP previews and output summaries."""
        project_observation(
            "benchmark", self.observation, "https://benchmark.invalid"
        ).model_dump_json()

    def track_preview_bytes(self, _rows: int) -> int:
        """Return the size of the bounded public projection."""
        return len(
            project_observation(
                "benchmark", self.observation, "https://benchmark.invalid"
            )
            .model_dump_json()
            .encode()
        )

    def teardown(self, _rows: int) -> None:
        """Restore worker globals after each measurement."""
        self.config_patch.stop()
        self.registry_patch.stop()
