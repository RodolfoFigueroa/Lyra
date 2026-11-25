"""Worker execution of serialized acceptance bindings with fake job storage."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock, patch

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.config import JobsConfig, LyraConfig
from lyra.contracts.jobs import AcceptedJob, JobEnvelope
from lyra.contracts.references import new_job_id
from lyra.server import worker
from lyra.server.registry import Catalog
from test_support.canonical_execution import canonical_workload
from test_support.receiving_workloads import receiving_workload


class BoundWorkerSuite:
    """Measure live worker preparation and execution after acceptance."""

    version = "3"

    def setup(self) -> None:
        """Prepare a two-input route and an in-process storage fake."""
        self.work = canonical_workload("routed", 2, 2)
        self.prepare_worker()

    def prepare_worker(self) -> None:
        """Bind the selected workload to temporary storage and fake job services."""
        self.native = SimpleNamespace(
            id=new_job_id(),
            origin="lyra",
            started_at=None,
            ended_at=None,
            worker_name="benchmark",
            meta={},
            save_meta=Mock(),
        )
        self.scratch = TemporaryDirectory()
        self.config = Mock(
            spec=LyraConfig,
            jobs=JobsConfig(),
            result_store_path=Path(self.scratch.name) / "results.sqlite3",
            resources={},
        )
        self.catalog = Catalog(self.work.executor.catalog)

        self.envelope = JobEnvelope(
            job_id=self.native.id,
            accepted=AcceptedJob(
                plan=WorkerRequest.from_plan(self.work.prepared),
                catalog_fingerprint="benchmark",
                plugin=self.catalog.compiled.manifests[
                    self.catalog.compiled.graph.nodes[
                        self.work.prepared.node
                    ].distribution
                ].plugin,
                created_at=datetime.now(UTC),
            ),
        )

    def time_execute(self) -> None:
        """Prepare and execute normalized values without queue connections."""
        with (
            patch.object(worker, "get_current_job", return_value=self.native),
            patch.object(
                worker,
                "_WORKER_RUNTIME",
                worker.WorkerRuntime(self.catalog, self.config),
            ),
            patch.object(worker, "build_run_context", return_value=Mock()),
        ):
            self.native.id = new_job_id()
            worker.run_metric_task(
                self.envelope.model_dump(mode="json") | {"job_id": self.native.id}
            )

    def peakmem_execute(self) -> None:
        """Measure whole-process memory through worker result persistence."""
        self.time_execute()

    def teardown(self) -> None:
        """Release temporary benchmark storage."""
        self.scratch.cleanup()


class ReceivingWorkerSuite:
    """Execute serialized receiving routes and persist outputs with caches off."""

    version = "1"
    params: ClassVar[list[int]] = [1, 1000]
    param_names: ClassVar[list[str]] = ["rows"]

    def setup(self, rows: int) -> None:
        """Reuse the isolated worker harness with a nested receiving workload."""
        self.worker = BoundWorkerSuite()
        self.worker.work = receiving_workload(rows, nested=True)
        self.worker.prepare_worker()

    def time_execute(self, _rows: int) -> None:
        """Revalidate acceptance, execute routes, and write the current result."""
        self.worker.time_execute()

    def peakmem_execute(self, rows: int) -> None:
        """Include the isolated worker setup and retained result in process peak."""
        self.time_execute(rows)

    def teardown(self, _rows: int) -> None:
        """Remove retained output and scratch files."""
        self.worker.teardown()
