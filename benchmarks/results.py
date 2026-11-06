"""Worker execution and bounded public result projections."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import Mock, patch

from lyra.contracts.config import LyraConfig
from lyra.runtime.artifacts import ArtifactStore
from lyra.server import worker
from lyra.server.result_projection import project_observation
from test_support.worker_workloads import result_workload


class ResultSuite:
    """Execute queued contracts and inspect complete retained native results."""

    version = "4"
    params: ClassVar[list[int]] = [1000, 10000]

    def setup(self, rows: int) -> None:
        """Prepare the worker workload and isolate its registry and artifact store."""
        self.temporary = TemporaryDirectory()
        self.artifacts = ArtifactStore(Path(self.temporary.name))
        self.fixture = result_workload(rows, self.artifacts)
        self.registry_patch = patch.dict(
            worker.RUNNER_REGISTRY, {"root": self.fixture.entry}, clear=True
        )
        self.config_patch = patch.object(
            worker,
            "require_worker_config",
            return_value=Mock(
                spec=LyraConfig,
                jobs=Mock(),
                artifact_dir=Path(self.temporary.name),
                resources={},
            ),
        )
        self.registry_patch.start()
        self.config_patch.start()

    def time_worker(self, _rows: int) -> None:
        """Validate accepted job bindings, execute, and serialize native outputs."""
        worker.execute_job(self.fixture.envelope, self.fixture.native)

    def time_preview(self, _rows: int) -> None:
        """Build bounded MCP previews and output summaries."""
        project_observation("benchmark", self.fixture.observation).model_dump_json()

    def track_preview_bytes(self, _rows: int) -> int:
        """Return the size of the bounded public projection."""
        return len(
            project_observation("benchmark", self.fixture.observation)
            .model_dump_json()
            .encode()
        )

    def teardown(self, _rows: int) -> None:
        """Restore worker globals after each measurement."""
        self.config_patch.stop()
        self.registry_patch.stop()
        self.temporary.cleanup()
