"""Worker execution of serialized acceptance bindings with fake job storage."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lyra.sdk.bound_plan import serialize_bound_plan
from lyra.sdk.models.job import JobEnvelope, JobRunProvenance

from benchmarks.execution import CanonicalExecutionSuite
from lyra_app import worker
from tests.config_helpers import load_test_config


class BoundWorkerSuite:
    """Measure the durable worker boundary independently of acceptance."""

    version = "2"

    def setup(self) -> None:
        """Prepare a two-input route and an in-process storage fake.

        Raises:
            ValueError: If the fixture is not a metric.
        """
        self.suite = CanonicalExecutionSuite()
        self.suite.setup("routed", 2, 2)
        self.payload = serialize_bound_plan(self.suite.prepared)
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
            job_id=self.native.id, metric="two", input={}, bound_plan=self.payload
        )
        if self.entry.contract.kind != "metric":
            message = "Benchmark requires a metric"
            raise ValueError(message)
        self.native.meta["provenance"] = JobRunProvenance(
            metric="two",
            bound_plan=self.payload,
            catalog_fingerprint="benchmark",
            plugin=self.suite.catalog.manifests["bench"].plugin,
            input={},
            outputs=self.entry.contract.outputs,
            created_at=datetime.now(UTC),
        ).model_dump(mode="json")

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
