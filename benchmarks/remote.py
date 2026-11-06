"""Worker execution of serialized acceptance bindings with fake job storage."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lyra.sdk.models.job import AcceptedJob, JobEnvelope

from benchmarks.execution import CanonicalExecutionSuite
from lyra_app import worker
from tests.config_helpers import load_test_config


class BoundWorkerSuite:
    """Measure the durable worker boundary independently of acceptance."""

    version = "3"

    def setup(self) -> None:
        """Prepare a two-input route and an in-process storage fake."""
        self.suite = CanonicalExecutionSuite()
        self.suite.setup("routed", 2, 2)
        self.native = SimpleNamespace(
            id="benchmark",
            origin="heavy",
            worker_name="benchmark",
            meta={},
            save_meta=Mock(),
        )
        self.scratch = TemporaryDirectory()
        self.config = load_test_config(Path(self.scratch.name))
        plugin = self.suite.executor.plugins["bench"]
        self.entry = worker.RunnerMetricEntry(
            "two",
            "heavy",
            self.suite.catalog.graph.nodes["two"].node,
            self.suite.catalog.manifests["bench"],
            "bench",
            self.suite.catalog.graph,
            {"bench": plugin},
            self.suite.executor.location_provider,
        )

        self.envelope = JobEnvelope(
            job_id=self.native.id,
            accepted=AcceptedJob(
                plan=self.suite.prepared,
                catalog_fingerprint="benchmark",
                plugin=self.suite.catalog.manifests["bench"].plugin,
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
