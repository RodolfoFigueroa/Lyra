"""Worker execution of serialized acceptance bindings with fake job storage."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lyra.contracts.config import JobsConfig, LyraConfig
from lyra.contracts.jobs import AcceptedJob, JobEnvelope
from lyra.server import worker
from test_support.canonical_execution import canonical_workload
from test_support.worker_workloads import canonical_worker_entry


class BoundWorkerSuite:
    """Measure the durable worker boundary independently of acceptance."""

    version = "3"

    def setup(self) -> None:
        """Prepare a two-input route and an in-process storage fake."""
        self.work = canonical_workload("routed", 2, 2)
        self.native = SimpleNamespace(
            id="benchmark",
            origin="heavy",
            worker_name="benchmark",
            meta={},
            save_meta=Mock(),
        )
        self.scratch = TemporaryDirectory()
        self.config = Mock(
            spec=LyraConfig,
            jobs=JobsConfig(),
            artifact_dir=Path(self.scratch.name) / "artifacts",
            resources={},
        )
        self.entry = canonical_worker_entry(self.work)

        self.envelope = JobEnvelope(
            job_id=self.native.id,
            accepted=AcceptedJob(
                plan=self.work.prepared,
                catalog_fingerprint="benchmark",
                plugin=self.entry.plugin.plugin,
                created_at=datetime.now(UTC),
            ),
        )

    def time_execute(self) -> None:
        """Deserialize and execute captured bindings without queue connections."""
        with (
            patch.object(worker, "get_current_job", return_value=self.native),
            patch.dict(worker.RUNNER_REGISTRY, {"two": self.entry}),
            patch.object(
                worker,
                "require_worker_config",
                return_value=self.config,
            ),
            patch.object(worker, "build_run_context", return_value=Mock()),
        ):
            worker.run_metric_task(self.envelope.model_dump(mode="json"))

    def teardown(self) -> None:
        """Release temporary benchmark storage."""
        self.scratch.cleanup()
