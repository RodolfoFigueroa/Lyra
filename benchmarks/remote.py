"""Worker execution of serialized acceptance bindings with fake job storage."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, patch

from lyra.sdk.bound_plan import serialize_bound_plan

from benchmarks.execution import CanonicalExecutionSuite
from lyra_app import worker
from tests.config_helpers import load_test_config

if TYPE_CHECKING:
    from rq.job import Job


class BoundWorkerSuite:
    """Measure the durable worker boundary independently of acceptance."""

    def setup(self) -> None:
        """Prepare a two-input route and an in-process storage fake."""
        self.suite = CanonicalExecutionSuite()
        self.suite.setup("routed", 2, 2)
        self.payload = serialize_bound_plan(self.suite.prepared)
        self.native = SimpleNamespace(
            id="benchmark", origin="heavy", meta={}, save_meta=Mock()
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
            plugin,
            self.suite.catalog.graph,
            {"bench": plugin},
            self.suite.executor.location_provider,
        )

    def time_execute(self) -> None:
        """Deserialize and execute captured bindings without queue connections."""
        with (
            patch.dict(worker.RUNNER_REGISTRY, {"two": self.entry}),
            patch.object(
                worker,
                "require_worker_config",
                return_value=self.config,
            ),
            patch.object(worker, "build_run_context", return_value=Mock()),
        ):
            worker.execute_bound_job(self.payload, cast("Job", self.native))

    def teardown(self) -> None:
        """Release temporary benchmark storage."""
        self.scratch.cleanup()
