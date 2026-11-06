"""In-process HTTP and MCP operations with isolated job storage."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import Mock

import httpx
from fastapi import FastAPI

from lyra.contracts.jobs import JobCreateRequest
from lyra.runtime.artifacts import ArtifactStore
from lyra.server.mcp.models import (
    GetJobResultInput,
    GetMetricInput,
    ListMetricsInput,
    SearchMetricsInput,
)
from lyra.server.mcp.tools import InProcessLyraBackend, execute_tool
from lyra.server.plugins import InstalledPlugin
from lyra.server.results import ResultService
from lyra.server.routes import jobs, metrics
from lyra.server.runtime import ApplicationRuntime, get_catalog, get_jobs, get_results
from test_support.worker_workloads import result_workload
from test_support.workloads import catalog, catalog_declarations


class InterfaceSuite:
    """Exercise actual routes and tool dispatch without service connections."""

    version = "4"
    params: ClassVar[list[int]] = [10, 1000]
    param_names: ClassVar[list[str]] = ["nodes"]

    def setup_cache(self) -> dict[int, list[InstalledPlugin]]:
        """Return installed declaration fixtures for each catalog size."""
        return catalog_declarations(self.params)

    def setup(self, declarations: dict[int, list[InstalledPlugin]], nodes: int) -> None:
        """Prepare transport, event loop, catalog, and a bounded job-store fake."""
        self.catalog = catalog(declarations[nodes])
        self.discovery = self.catalog.discovery
        self.jobs = Mock()
        self.jobs.enqueue.side_effect = lambda *_, **__: self.jobs.reset_mock()
        self.runtime = Mock(
            spec=ApplicationRuntime, catalog=self.catalog, jobs=self.jobs
        )
        self.backend = InProcessLyraBackend(lambda: self.runtime)
        self.temporary = TemporaryDirectory()
        artifacts = ArtifactStore(Path(self.temporary.name))
        self.results = result_workload(1000, artifacts)

        self.jobs.read_job.return_value = self.results.observation
        service = ResultService(self.jobs, artifacts)
        self.runtime.results = service
        self.result_request = GetJobResultInput(result_ref="lyra://results/benchmark")
        self.loop = asyncio.Runner()
        app = FastAPI()
        app.state.runtime = Mock(
            spec=ApplicationRuntime, credentials=Mock(agent_api_key="fixture-key")
        )
        app.include_router(metrics.router)
        app.include_router(jobs.router)
        app.dependency_overrides[get_catalog] = lambda: self.catalog
        app.dependency_overrides[get_jobs] = lambda: self.jobs
        app.dependency_overrides[get_results] = lambda: service
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://benchmark.invalid",
            headers={"Authorization": "Bearer fixture-key"},
        )
        self.request = JobCreateRequest(metric="source", input={})
        self.search = SearchMetricsInput(query="population")
        self.listing = ListMetricsInput()
        self.inspection = GetMetricInput(metric="source")

    def time_rest_submit(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Validate, enqueue through the thread boundary, and serialize HTTP."""
        response = self.loop.run(
            self.client.post("/jobs", json=self.request.model_dump())
        )
        response.raise_for_status()

    def time_rest_list(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Build and serialize the catalog through HTTP routing."""
        self.loop.run(self.client.get("/metrics")).raise_for_status()

    def time_rest_inspect(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Inspect a metric through HTTP routing."""
        self.loop.run(self.client.get("/metrics/source")).raise_for_status()

    def time_mcp_search(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Search and validate a compact MCP response."""
        self.loop.run(
            execute_tool(
                "lyra_search_metrics",
                self.search,
                self.backend,
                public_api_base_url="https://benchmark.invalid",
            )
        ).model_dump_json()

    def time_mcp_list(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """List a bounded MCP catalog page."""
        self.loop.run(
            execute_tool(
                "lyra_list_metrics",
                self.listing,
                self.backend,
                public_api_base_url="https://benchmark.invalid",
            )
        ).model_dump_json()

    def time_mcp_inspect(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Inspect the executable metric contract."""
        self.loop.run(
            execute_tool(
                "lyra_get_metric",
                self.inspection,
                self.backend,
                public_api_base_url="https://benchmark.invalid",
            )
        ).model_dump_json()

    def track_mcp_list_bytes(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> int:
        """Return the compact structured discovery response size in bytes."""
        return len(
            self.loop.run(
                execute_tool(
                    "lyra_list_metrics",
                    self.listing,
                    self.backend,
                    public_api_base_url="https://benchmark.invalid",
                )
            )
            .model_dump_json()
            .encode()
        )

    def time_mcp_submit(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Submit through the real in-process backend."""
        self.loop.run(
            execute_tool(
                "lyra_run_metric",
                self.request,
                self.backend,
                public_api_base_url="https://benchmark.invalid",
            )
        ).model_dump_json()

    def time_rest_result(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Serialize a retained result through the HTTP route."""
        self.loop.run(self.client.get("/jobs/benchmark/result")).raise_for_status()

    def time_mcp_result(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Project a retained result through MCP tool dispatch."""
        self.loop.run(
            execute_tool(
                "lyra_get_job_result",
                self.result_request,
                self.backend,
                public_api_base_url="https://benchmark.invalid",
            )
        ).model_dump_json()

    def teardown(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Close transport and executor resources."""
        self.loop.run(self.client.aclose())
        self.loop.close()
        self.temporary.cleanup()
