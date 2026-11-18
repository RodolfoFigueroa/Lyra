from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import httpx
import pytest
from fastapi import Request
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus
from starlette.applications import Starlette
from starlette.routing import Mount

from docs.scripts.mcp_tools import advertised_tools
from lyra.api import AsyncLyraClient, LyraClient
from lyra.contracts.errors import ReportNotFoundError, ResultNotFoundError
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import (
    FailedJobResult,
    JobCreateRequest,
    JobCreateResponse,
    JobLifecycleStatus,
    JobLinks,
    JobProgress,
    JobStatusInfo,
    ResultLifetime,
    TableJobResult,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    MetricManifest,
    PluginManifest,
)
from lyra.contracts.types import (
    JsonObject,
    JsonValue,
    validate_json_object,
    validate_json_value,
)
from lyra.contracts.units import Unit
from lyra.runtime.planning.discovery import Discovery
from lyra.server import main
from lyra.server.job_store import InvalidJobResultError, JobObservation
from lyra.server.mcp import SERVER_INSTRUCTIONS, tools
from lyra.server.mcp import create_mcp_app as _create_mcp_app
from lyra.server.mcp.models import (
    GetJobResultInput,
    GetMetricDependenciesInput,
    GetMetricInput,
    JobIdInput,
    ListMetricsInput,
    SearchMetricsInput,
)
from lyra.server.mcp.tools import InProcessLyraBackend, ToolCallError
from lyra.server.results import ResultService
from lyra.server.runtime import get_runtime
from test_support.location_helpers import node_contract
from test_support.location_resolution import SELECTED, selection
from test_support.manifest_catalog import compile_catalog
from test_support.mcp import call_tool
from tests.config_helpers import api_credentials, load_test_config
from tests.contract_helpers import (
    NestedDefaultParameters,
    metric_manifest,
    plugin_manifest,
)
from tests.mcp_helpers import (
    ManagedTestClient,
    mcp_headers,
    submit_both,
    tool_call_payload,
    tool_payload,
)
from tests.provenance_helpers import result_provenance
from tests.queue_helpers import submitted_input
from tests.result_helpers import result_observation
from tests.retained_job_helpers import retain

pytestmark = pytest.mark.usefixtures("dummy_redis_clients", "_close_managed_clients")

_COMPLETED_AT = datetime(2026, 7, 9, 12, 5, tzinfo=UTC)
create_mcp_app = partial(
    _create_mcp_app,
    public_api_base_url="https://lyra.example.test/api",
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from lyra.contracts.config import LyraConfig
    from lyra.contracts.reports import ExecutionReport
    from lyra.contracts.results import ResultDescription, ResultRowPage
    from lyra.server.result_rows import RowPageContext


def _initialize_payload() -> JsonObject:
    return validate_json_object(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )


def _metric_cursor_payload(
    *,
    fingerprint: str = "catalog-1",
    offset: int = 1,
    version: int = 1,
) -> str:
    payload = json.dumps(
        {"fingerprint": fingerprint, "offset": offset, "version": version},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


_MANAGED_CLIENTS: list[ManagedTestClient] = []


def managed_client(app: Starlette) -> ManagedTestClient:
    client = ManagedTestClient(app)
    _MANAGED_CLIENTS.append(client)
    return client


@pytest.fixture
def _close_managed_clients() -> Iterator[None]:
    yield
    while _MANAGED_CLIENTS:
        _MANAGED_CLIENTS.pop().close()


def _app_with_mcp(
    config: LyraConfig,
) -> ManagedTestClient:
    return managed_client(main.create_app(config, credentials=api_credentials()))


class FakeMCPBackend:
    async def rows(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        del limit, cursor, context
        self.observed.append(result_ref)
        raise ResultNotFoundError(result_ref, output_name)

    async def location_input_rows(
        self,
        result_ref: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        del limit, cursor, context
        self.observed.append(result_ref)
        raise ResultNotFoundError(result_ref, input_name=input_name)

    async def validate_location_input(self, result_ref: str, input_name: str) -> None:
        self.observed.append(result_ref)
        raise ResultNotFoundError(result_ref, input_name=input_name)

    async def validate_table(self, result_ref: str, output_name: str) -> None:
        self.observed.append(result_ref)
        raise ResultNotFoundError(result_ref, output_name)

    async def read_report(self, execution_id: str) -> ExecutionReport:
        self.observed.append(execution_id)
        raise ReportNotFoundError(execution_id)

    async def describe_result(self, result_ref: str) -> ResultDescription:
        self.observed.append(result_ref)
        raise ResultNotFoundError(result_ref)

    def __init__(self, metrics: list[MetricManifest]) -> None:
        self.catalog = SimpleNamespace(
            client_schema_version=4,
            json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
            catalog_fingerprint="catalog-1",
            metrics=metrics,
        )
        self.jobs: dict[str, JobStatusInfo] = {}
        self.observations: dict[str, JobObservation] = {}
        self.observed: list[str] = []
        self.payloads: list[dict[str, Any]] = []
        self.job_status_sequence: list[JobLifecycleStatus] = ["succeeded"]

    async def get_discovery(self) -> Discovery:
        if not self.catalog.metrics:
            view = Discovery(compile_catalog({}))
            view.fingerprint = self.catalog.catalog_fingerprint
            return view
        nodes = self.catalog.metrics
        view = Discovery(
            compile_catalog(
                {
                    "fake": PluginManifest.model_validate(
                        {
                            "plugin": {"name": "fake", "version": "1"},
                            "factory": "fake:create",
                            "nodes": nodes,
                        }
                    )
                }
            )
        )
        view.fingerprint = self.catalog.catalog_fingerprint
        return view

    async def create_job(
        self,
        request: JobCreateRequest,
    ) -> JobCreateResponse:
        metric, payload = (
            request.metric,
            {
                **request.inputs,
                **(
                    {"parameters": request.parameters}
                    if "parameters" in request.model_fields_set
                    else {}
                ),
            },
        )
        if not any(item.name == metric for item in self.catalog.metrics):
            code = "unknown_metric"
            raise ToolCallError(code, f"Unknown metric: {metric}")
        if request.parameters.get("value") == "invalid":
            code = "invalid_parameters"
            message = "Invalid metric parameters."
            details = [
                {
                    "loc": ["parameters", "value"],
                    "msg": "Expected integer.",
                    "type": "type",
                }
            ]
            raise self._tool_error(
                code,
                message,
                validate_json_value(details),
            )

        job_id = f"job-{len(self.payloads) + 1}"
        self.payloads.append(payload)
        self.jobs[job_id] = self.job_status(job_id, self.job_status_sequence[0], metric)
        self.observations[job_id] = JobObservation(snapshot=self.jobs[job_id])
        return self._job_response(job_id, metric)

    @staticmethod
    def _job_response(
        job_id: str,
        metric: str,
    ) -> JobCreateResponse:
        return JobCreateResponse(
            job_id=job_id,
            metric=metric,
            status="queued",
            links=JobLinks(
                self=f"/jobs/{job_id}",
                result=f"/jobs/{job_id}/result",
            ),
        )

    async def observe_job(self, job_id: str) -> JobObservation | None:
        self.observed.append(job_id)
        return self.observations.get(job_id)

    @staticmethod
    def job_status(
        job_id: str,
        status: JobLifecycleStatus,
        metric: str | None,
    ) -> JobStatusInfo:
        return JobStatusInfo(
            job_id=job_id,
            status=status,
            execution_id="b" * 32 if status == "succeeded" else None,
            result_ref="a" * 32 if status == "succeeded" else None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metric=metric,
        )

    @staticmethod
    def _tool_error(code: str, message: str, details: JsonValue) -> Exception:
        return ToolCallError(code, message, details)


def _table_metric(
    name: str,
    description: str,
    *,
    spatial_inputs: dict[str, str] | None = None,
    value_type: str = "integer",
) -> MetricManifest:
    spatial = spatial_inputs or {"location": "location"}
    properties: dict[str, Any] = {
        field: LocationSelection.model_json_schema() for field in spatial
    }
    properties["parameters"] = {
        "type": "object",
        "properties": {
            "value": {
                "type": value_type,
                "description": "Value copied into each output row.",
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }
    return MetricManifest(
        code_revision="1",
        name=name,
        description=description,
        parameters_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **properties["parameters"],
        },
        location_inputs={
            key: LocationInputDeclaration(contract=node_contract()) for key in spatial
        },
        outputs={
            name: OutputMetadata(
                kind="table",
                columns=[
                    ColumnMetadata(
                        name="value",
                        dtype="int64",
                        unit=Unit.COUNT,
                        description="Submitted value.",
                    )
                ],
            )
        },
    )


def _parameterless_metric(name: str, description: str) -> MetricManifest:
    return MetricManifest(
        code_revision="1",
        name=name,
        description=description,
        location_inputs={
            "location": LocationInputDeclaration(contract=node_contract())
        },
        outputs={name: OutputMetadata(columns=[])},
    )


def test_mcp_package_initializes_with_bearer_auth() -> None:
    app = create_mcp_app(
        agent_api_key="agent-secret",
        backend=FakeMCPBackend([]),
    )
    client = managed_client(app)

    missing = client.post("/", json=_initialize_payload())
    invalid = client.post(
        "/",
        json=_initialize_payload(),
        headers=mcp_headers("wrong"),
    )
    initialized = client.post(
        "/",
        json=_initialize_payload(),
        headers=mcp_headers(),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403
    assert initialized.status_code == 200
    result = initialized.json()["result"]
    assert result["serverInfo"]["name"] == "lyra"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["instructions"] == SERVER_INSTRUCTIONS


def test_official_client_initializes_lists_calls_and_closes_cleanly() -> None:
    metric = _table_metric("smoke_table_metric", "Return a table.")
    mcp_app = create_mcp_app(
        agent_api_key="agent-secret",
        backend=FakeMCPBackend([metric]),
    )
    mounted_app = Starlette(routes=[Mount("/mcp", app=mcp_app)])

    async def use_official_client() -> tuple[Any, Any, Any, Any, Any, Any]:
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app),
                base_url="http://testserver",
                headers=mcp_headers(),
                follow_redirects=True,
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            called = await session.call_tool(
                "lyra_get_metric",
                {"metric": "smoke_table_metric"},
            )
            extra_argument = await session.call_tool(
                "lyra_get_metric",
                {"metric": "smoke_table_metric", "unexpected": True},
            )
            invalid_type = await session.call_tool(
                "lyra_search_metrics",
                {"query": "smoke", "limit": "2"},
            )
            unknown = await session.call_tool("lyra_not_a_tool", {})
            return (
                initialized,
                tools,
                called,
                extra_argument,
                invalid_type,
                unknown,
            )

    (
        initialized,
        tools,
        called,
        extra_argument,
        invalid_type,
        unknown,
    ) = asyncio.run(use_official_client())

    assert initialized.serverInfo.name == "lyra"
    assert initialized.instructions == SERVER_INSTRUCTIONS
    tool_names = {tool.name for tool in tools.tools}
    assert tool_names == {
        "lyra_get_metric_dependencies",
        "lyra_download_result",
        "lyra_download_table",
        "lyra_download_location_input",
        "lyra_get_location_input_rows",
        "lyra_get_result_rows",
        "lyra_describe_result",
        "lyra_inspect_report",
        "lyra_get_job_result",
        "lyra_search_metrics",
        "lyra_get_metric",
        "lyra_list_metrics",
        "lyra_run_metric",
    }
    run_tool = next(tool for tool in tools.tools if tool.name == "lyra_run_metric")
    assert "Submit once" in (run_tool.description or "")
    assert all(
        "wait_seconds" not in tool.inputSchema["properties"] for tool in tools.tools
    )
    for tool in tools.tools:
        assert tool.inputSchema["type"] == "object"
        assert tool.outputSchema is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is False
        if tool.name == "lyra_run_metric":
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.idempotentHint is False
            assert tool.annotations.openWorldHint is True
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.idempotentHint is True
            assert tool.annotations.openWorldHint is False
    assert called.isError is False
    assert called.structuredContent is not None
    assert called.structuredContent["metric"]["name"] == "smoke_table_metric"
    assert extra_argument.isError is True
    assert extra_argument.content
    assert invalid_type.isError is True
    assert invalid_type.content
    assert unknown.isError is True
    assert unknown.content


def test_streamable_http_transport_enforces_sdk_request_rules() -> None:
    client = managed_client(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([]),
        )
    )

    invalid_origin = client.post(
        "/",
        json=_initialize_payload(),
        headers={**mcp_headers(), "Origin": "https://attacker.example"},
    )
    invalid_protocol = client.post(
        "/",
        json=validate_json_object({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        headers={**mcp_headers(), "MCP-Protocol-Version": "1900-01-01"},
    )
    notification = client.post(
        "/",
        json=validate_json_object(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ),
        headers=mcp_headers(),
    )
    invalid_content_type = client.post(
        "/",
        content="{}",
        headers={**mcp_headers(), "Content-Type": "text/plain"},
    )
    invalid_get_accept = client.get(
        "/",
        headers={**mcp_headers(), "Accept": "application/json"},
    )

    assert invalid_origin.status_code == 403
    assert invalid_protocol.status_code == 400
    assert "Unsupported protocol version" in invalid_protocol.text
    assert notification.status_code == 202
    assert invalid_content_type.status_code == 400
    assert invalid_get_accept.status_code == 406


def test_streamable_http_transport_allows_public_api_host() -> None:
    client = managed_client(
        _create_mcp_app(
            agent_api_key="agent-secret",
            public_api_base_url="https://lyra.example.test/api",
            backend=FakeMCPBackend([]),
        )
    )

    public_host = client.post(
        "/",
        json=_initialize_payload(),
        headers={**mcp_headers(), "Host": "lyra.example.test"},
    )
    public_host_with_port = client.post(
        "/",
        json=_initialize_payload(),
        headers={**mcp_headers(), "Host": "lyra.example.test:443"},
    )
    unexpected_host = client.post(
        "/",
        json=_initialize_payload(),
        headers={**mcp_headers(), "Host": "attacker.example"},
    )

    assert public_host.status_code == 200
    assert public_host_with_port.status_code == 200
    assert unexpected_host.status_code == 421


def test_mcp_search_metrics_ranks_public_catalog_candidates() -> None:
    backend = FakeMCPBackend(
        [
            _parameterless_metric("smoke_summary_metric", "Summarize source records."),
            _table_metric(
                "accessibility_score",
                "Measure access to clinics and services.",
            ),
            _table_metric("population_count", "Count residents by area."),
        ]
    )
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_search_metrics",
            {"query": "accessibility", "limit": 2},
        ),
        headers=mcp_headers(),
    )

    payload = tool_payload(response)
    assert payload["catalog_fingerprint"] == "catalog-1"
    assert [candidate["name"] for candidate in payload["candidates"]] == [
        "accessibility_score",
    ]
    first = payload["candidates"][0]
    assert "accepted_selections" in first


def test_mcp_search_metrics_does_not_treat_tokenless_query_as_inventory() -> None:
    client = managed_client(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("population", "Residents.")]),
        )
    )

    payload = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_search_metrics", {"query": "?!"}),
            headers=mcp_headers(),
        )
    )

    assert payload["candidates"] == []


@pytest.mark.parametrize("limit", [0, 100])
@pytest.mark.parametrize(
    ("tool_name", "base_arguments"),
    [
        ("lyra_search_metrics", {"query": "population"}),
        ("lyra_list_metrics", {}),
    ],
)
def test_metric_discovery_rejects_out_of_bounds_limits(
    tool_name: str,
    base_arguments: dict[str, Any],
    limit: int,
) -> None:
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            tool_name,
            {**base_arguments, "limit": limit},
        ),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True


def test_metric_discovery_contracts_route_inventory_and_search() -> None:
    list_contract = advertised_tools()["lyra_list_metrics"]
    search_contract = advertised_tools()["lyra_search_metrics"]

    assert search_contract.inputSchema["properties"]["limit"]["maximum"] == 20
    assert search_contract.inputSchema["properties"]["limit"]["default"] == 5
    assert list_contract.inputSchema["properties"]["limit"]["default"] == 20


def test_mcp_list_metrics_returns_compact_paginated_inventory() -> None:
    long_description = f"  First\nmetric  {'x' * 300}  "
    metrics = [
        _table_metric(
            f"metric_{index:02d}",
            long_description if index == 0 else f"Metric {index}.",
        )
        for index in reversed(range(22))
    ]
    client = managed_client(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend(metrics),
        )
    )

    first = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_list_metrics", {}),
            headers=mcp_headers(),
        )
    )
    assert first["catalog_fingerprint"] == "catalog-1"
    assert first["total_count"] == 22
    assert [metric["name"] for metric in first["metrics"]] == [
        f"metric_{index:02d}" for index in range(20)
    ]
    assert len(first["metrics"][0]["description"]) == 240
    assert "\n" not in first["metrics"][0]["description"]
    assert first["metrics"][0]["description"].endswith("…")
    assert first["next_cursor"]

    second = tool_payload(
        client.post(
            "/",
            json=tool_call_payload(
                "lyra_list_metrics",
                {"cursor": first["next_cursor"], "limit": 1},
            ),
            headers=mcp_headers(),
        )
    )
    assert [metric["name"] for metric in second["metrics"]] == ["metric_20"]
    assert second["next_cursor"]

    third = tool_payload(
        client.post(
            "/",
            json=tool_call_payload(
                "lyra_list_metrics",
                {"cursor": second["next_cursor"]},
            ),
            headers=mcp_headers(),
        )
    )
    assert [metric["name"] for metric in third["metrics"]] == ["metric_21"]
    assert third["next_cursor"] is None


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor!",
        _metric_cursor_payload(version=2),
        _metric_cursor_payload(offset=0),
        _metric_cursor_payload(offset=2),
    ],
)
def test_mcp_list_metrics_rejects_invalid_cursors(cursor: str) -> None:
    client = managed_client(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend([_table_metric("metric", "A metric.")]),
        )
    )

    response = client.post(
        "/",
        json=tool_call_payload("lyra_list_metrics", {"cursor": cursor}),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "invalid_cursor"
    assert "without cursor" in result["structuredContent"]["error"]["details"]["action"]


def test_mcp_list_metrics_rejects_cursor_after_catalog_change() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric("metric_a", "First metric."),
            _table_metric("metric_b", "Second metric."),
        ]
    )
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    first = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_list_metrics", {"limit": 1}),
            headers=mcp_headers(),
        )
    )
    backend.catalog.catalog_fingerprint = "catalog-2"

    response = client.post(
        "/",
        json=tool_call_payload("lyra_list_metrics", {"cursor": first["next_cursor"]}),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    details = result["structuredContent"]["error"]["details"]
    assert details["reason"] == "The metric catalog changed between pages."


def test_mcp_list_metrics_handles_empty_and_exact_boundary_catalogs() -> None:
    empty_client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )
    empty = tool_payload(
        empty_client.post(
            "/",
            json=tool_call_payload("lyra_list_metrics", {}),
            headers=mcp_headers(),
        )
    )
    assert empty["total_count"] == 0
    assert empty["metrics"] == []
    assert empty["next_cursor"] is None

    exact_client = managed_client(
        create_mcp_app(
            agent_api_key="agent-secret",
            backend=FakeMCPBackend(
                [
                    _table_metric("metric_b", "Second."),
                    _table_metric("metric_a", "First."),
                ]
            ),
        )
    )
    exact = tool_payload(
        exact_client.post(
            "/",
            json=tool_call_payload("lyra_list_metrics", {"limit": 2}),
            headers=mcp_headers(),
        )
    )
    assert [metric["name"] for metric in exact["metrics"]] == [
        "metric_a",
        "metric_b",
    ]
    assert exact["next_cursor"] is None


def test_official_client_supports_normalized_discovery() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "tree_coverage",
                "Cobertura de árboles urbanos.",
            ),
            _table_metric(
                "population_density",
                "Population density by neighborhood.",
            ),
            _table_metric(
                "heat_risk_index",
                "Heat risk by neighborhood.",
            ),
        ]
    )
    mcp_app = create_mcp_app(agent_api_key="agent-secret", backend=backend)
    mounted_app = Starlette(routes=[Mount("/mcp", app=mcp_app)])

    async def exercise_discovery() -> tuple[Any, ...]:
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app),
                base_url="http://testserver",
                headers=mcp_headers(),
                follow_redirects=True,
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            calls = (
                ("lyra_search_metrics", {"query": "tree coverage"}),
                ("lyra_search_metrics", {"query": "heat risk"}),
                ("lyra_search_metrics", {"query": "population density"}),
                ("lyra_search_metrics", {"query": "arboles"}),
                ("lyra_search_metrics", {"query": "value value"}),
                ("lyra_search_metrics", {"query": "value value"}),
                ("lyra_search_metrics", {"query": "value"}),
            )
            results = [
                await session.call_tool(name, arguments) for name, arguments in calls
            ]
            return (tools, *results)

    results = asyncio.run(exercise_discovery())

    for result, expected_metric in (
        (results[1], "tree_coverage"),
        (results[2], "heat_risk_index"),
        (results[3], "population_density"),
        (results[4], "tree_coverage"),
    ):
        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["candidates"][0]["name"] == expected_metric

    assert results[5].structuredContent is not None
    assert results[6].structuredContent is not None
    assert results[7].structuredContent is not None
    repeated_candidates = results[5].structuredContent["candidates"]
    assert repeated_candidates == results[6].structuredContent["candidates"]
    assert repeated_candidates == results[7].structuredContent["candidates"]
    assert repeated_candidates == []

    search_tool = next(
        tool for tool in results[0].tools if tool.name == "lyra_search_metrics"
    )
    for tool in (search_tool,):
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False


def test_mcp_get_metric_returns_public_contract() -> None:
    metric = _table_metric("smoke_table_metric", "Return a table.")
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([metric]))
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_get_metric",
            {"metric": "smoke_table_metric"},
        ),
        headers=mcp_headers(),
    )

    payload = tool_payload(response)
    assert payload["metric"]["name"] == "smoke_table_metric"
    assert set(payload["native_inputs"]) == {"location"}
    assert payload["outputs"][payload["metric"]["name"]]["kind"] == "table"


def test_mcp_preserves_methodology_for_inspection_and_search() -> None:
    description = (
        "Count the observations supplied for each input polygon.\n\n"
        "This synthetic example repeats the supplied integer count for every "
        "input feature. The value parameter specifies the count, and the output "
        "contains one row for each polygon, preserving feature identifiers. "
        "Zero represents a count of zero.\n\n"
        "Interpret each row independently: summing rows repeats the same "
        "supplied count rather than measuring a combined total."
    )
    metric = _table_metric("observation_count", description)
    parameter_description = (
        "Observation count to repeat for each polygon.\n"
        "Zero represents an observed count of zero."
    )
    column_description = "Supplied count per polygon.\nZero does not mean missing."
    value_schema = metric.parameters_schema
    assert value_schema is not None
    for key in ("properties", "value"):
        nested = value_schema[key]
        assert isinstance(nested, dict)
        value_schema = nested
    value_schema["description"] = parameter_description
    assert isinstance(metric.outputs[metric.name], OutputMetadata)
    metric.outputs[metric.name].columns[0].description = column_description
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([metric]))
    )
    inventory = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_list_metrics", {}),
            headers=mcp_headers(),
        )
    )
    compact = inventory["metrics"][0]["description"]
    assert len(compact) == 240
    assert compact.startswith(description.split("\n", maxsplit=1)[0])
    assert compact.endswith("…")
    detail = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_get_metric", {"metric": metric.name}),
            headers=mcp_headers(),
        )
    )
    assert detail["metric"]["description"] == description
    assert (
        detail["outputs"][metric.name]["columns"][0]["description"]
        == column_description
    )
    assert (
        detail["parameters"]["properties"]["value"]["description"]
        == parameter_description
    )
    assert "independently" not in compact
    search = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_search_metrics", {"query": "independently"}),
            headers=mcp_headers(),
        )
    )
    assert [candidate["name"] for candidate in search["candidates"]] == [metric.name]
    assert search["candidates"][0]["description"] == compact


def test_mcp_run_metric_passes_location_request_and_returns_submission() -> None:
    backend = FakeMCPBackend([_table_metric("smoke_table_metric", "Return a table.")])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_table_metric",
                "inputs": {
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    }
                },
                "parameters": {"value": 7},
            },
        ),
        headers=mcp_headers(),
    )

    payload = tool_payload(response)
    assert backend.payloads[0] == {
        "parameters": {"value": 7},
        "location": {"collection": "test:units", "unit_type": "lot", "ids": ["09.01"]},
    }
    assert payload["job_id"] == "job-1"
    assert "status" not in payload
    assert "reused" not in payload
    assert backend.observed == []


def test_mcp_run_metric_passes_bounds_request() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "smoke_bounds_metric",
                "Return a bounds table.",
                spatial_inputs={"bounds": "location"},
            )
        ]
    )
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_bounds_metric",
                "inputs": {
                    "bounds": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["13.02"],
                    }
                },
                "parameters": {"value": 3},
            },
        ),
        headers=mcp_headers(),
    )

    payload = tool_payload(response)
    assert "status" not in payload
    assert backend.payloads[0] == {
        "parameters": {"value": 3},
        "bounds": {"collection": "test:units", "unit_type": "lot", "ids": ["13.02"]},
    }


def test_mcp_run_metric_never_observes_completion() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued"]
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "slow_metric",
                "inputs": {
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    }
                },
                "parameters": {"value": 7},
            },
        ),
        headers=mcp_headers(),
    )

    payload = tool_payload(response)
    assert payload == {
        "job_id": "job-1",
        "next_tool": "lyra_get_job_result",
    }


def test_mcp_run_metric_creates_independent_submissions() -> None:
    backend = FakeMCPBackend([_table_metric("slow_metric", "Return later.")])
    backend.job_status_sequence = ["queued"]
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    arguments = {
        "metric": "slow_metric",
        "inputs": {
            "location": {
                "collection": "test:units",
                "unit_type": "lot",
                "ids": ["09.01"],
            }
        },
        "parameters": {"value": 7},
    }

    first = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_run_metric", arguments),
            headers=mcp_headers(),
        )
    )
    replay = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_run_metric", arguments),
            headers=mcp_headers(),
        )
    )

    assert (first["job_id"], replay["job_id"]) == ("job-1", "job-2")
    assert backend.observed == []
    assert first["job_id"] != replay["job_id"]
    assert [payload for payload in backend.payloads if "_poll" not in payload] == [
        {
            "parameters": {"value": 7},
            "location": {
                "collection": "test:units",
                "unit_type": "lot",
                "ids": ["09.01"],
            },
        }
    ] * 2


def test_mcp_run_metric_surfaces_unknown_metric_as_tool_error() -> None:
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=FakeMCPBackend([]))
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "missing",
                "inputs": {
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    }
                },
                "parameters": {},
            },
        ),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "unknown_metric"


def test_mcp_run_metric_surfaces_invalid_parameters_as_tool_error() -> None:
    backend = FakeMCPBackend(
        [_table_metric("smoke_table_metric", "Return a table.", value_type="integer")]
    )
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "smoke_table_metric",
                "inputs": {
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    }
                },
                "parameters": {"value": "invalid"},
            },
        ),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "invalid_parameters"
    assert error["details"][0]["loc"] == ["parameters", "value"]


def test_mcp_run_metric_accepts_multiple_spatial_inputs() -> None:
    backend = FakeMCPBackend(
        [
            _table_metric(
                "multi_spatial_metric",
                "Needs multiple shapes.",
                spatial_inputs={"location": "location", "bounds": "location"},
            )
        ]
    )
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )

    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "multi_spatial_metric",
                "inputs": {
                    "bounds": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["13.02"],
                    },
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    },
                },
                "parameters": {"value": 1},
            },
        ),
        headers=mcp_headers(),
    )

    result = response.json()["result"]
    assert result.get("isError", False) is False
    assert set(backend.payloads[0]) == {"location", "bounds", "parameters"}


def test_main_mounts_mcp_when_enabled(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    client = _app_with_mcp(config)

    response = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=mcp_headers(),
    )

    assert response.status_code == 200
    assert response.json()["result"]["instructions"] == SERVER_INSTRUCTIONS


def test_main_mcp_mount_requires_dedicated_token(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    client = _app_with_mcp(config)

    missing = client.post("/mcp/", json=_initialize_payload())
    admin_token = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=mcp_headers("admin-secret"),
    )

    assert missing.status_code == 401
    assert admin_token.status_code == 403


def test_main_does_not_mount_mcp_when_disabled(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    client = _app_with_mcp(config)

    response = client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=mcp_headers(),
    )

    assert response.status_code == 404


def test_main_shares_runtime_with_rest_and_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config.mcp.enabled = True
    factory = Mock(wraps=_create_mcp_app)
    monkeypatch.setattr(main, "create_mcp_app", factory)

    app = main.create_app(config, credentials=api_credentials())

    factory.assert_called_once()
    backend = factory.call_args.kwargs["backend"]
    assert isinstance(backend, InProcessLyraBackend)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            runtime = backend.runtime()
            assert runtime is app.state.runtime
            request = Request({"type": "http", "app": app})
            assert get_runtime(request) is runtime

    asyncio.run(exercise())


def _inspect(
    backend: FakeMCPBackend, job_id: str = "job-1", *, tool: str = "lyra_get_job_result"
) -> dict[str, Any]:
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=tool_call_payload(tool, {"job_id": job_id}),
        headers=mcp_headers(),
    )
    tool_payload(response)
    wire = response.json()["result"]
    if not wire["isError"]:
        validate(wire["structuredContent"], advertised_tools()[tool].outputSchema)
    return wire


@pytest.mark.parametrize("status", ["queued", "running", "failed", "succeeded"])
def test_status_only_observations(status: JobLifecycleStatus) -> None:
    snapshot = FakeMCPBackend.job_status("job-1", status, "metric")
    jobs = Mock(read_job=Mock(return_value=JobObservation(snapshot=snapshot)))
    artifacts = Mock()
    runtime = Mock(results=ResultService(jobs, artifacts))
    backend = InProcessLyraBackend(lambda: runtime)
    wire = asyncio.run(call_tool(backend, "lyra_get_job_result", {"job_id": "job-1"}))
    payload = (
        wire["structuredContent"]
        if wire["isError"]
        else wire["structuredContent"]["result"]
    )
    jobs.read_job.assert_called_once_with("job-1")
    artifacts.inspect.assert_not_called()
    if status == "succeeded":
        assert wire["isError"] is True
        assert payload["error"]["code"] == "result_observation_error"
    else:
        assert wire["isError"] is False
        assert payload["status"] == status
        if status in {"queued", "running"}:
            assert payload["poll_after_seconds"] == 2
            assert payload["metric"] == "metric"
            assert payload["created_at"] == snapshot.created_at.isoformat().replace(
                "+00:00", "Z"
            )
        else:
            assert payload["completed_at"] is None
            assert list(payload["outputs"]) == (
                ["result"] if payload["status"] == "succeeded" else []
            )


@pytest.mark.parametrize("snapshot_status", ["queued", "running"])
def test_lifecycle_is_authoritative(snapshot_status: JobLifecycleStatus) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        snapshot=backend.job_status("job-1", snapshot_status, "metric"),
    )
    payload = _inspect(backend)["structuredContent"]["result"]
    assert payload["status"] == snapshot_status


@pytest.mark.parametrize("result_type", [FailedJobResult])
def test_execution_failures_are_successful_observations(
    result_type: type[FailedJobResult],
) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "failed", "metric"),
        result=result_type(job_id="job-1", error={"message": "failed"}),
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    assert wire["structuredContent"]["result"]["error"]["message"] == "failed"
    download = _inspect(backend, tool="lyra_download_result")
    assert download["isError"] is True
    assert download["structuredContent"]["error"]["code"] == "result_not_downloadable"


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
def test_unknown_or_expired_job(tool: str) -> None:
    wire = _inspect(FakeMCPBackend([]), tool=tool)
    assert wire["isError"] is True
    assert wire["structuredContent"]["error"]["code"] == "result_not_found"


@pytest.mark.parametrize(
    "tool",
    [
        "lyra_get_result_metadata",
        "lyra_get_result_preview",
        "lyra_get_job_result",
        "lyra_run_metric",
    ],
)
def test_removed_tools_and_wait_arguments_are_rejected(tool: str) -> None:
    backend = FakeMCPBackend([])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    arguments = (
        {"job_id": "job-1", "wait_seconds": 0}
        if tool != "lyra_run_metric"
        else {
            "metric": "metric",
            "wait_seconds": 0,
            "inputs": {
                "location": {
                    "collection": "test:units",
                    "unit_type": "lot",
                    "ids": ["09.01"],
                }
            },
            "parameters": {},
        }
    )
    response = client.post(
        "/", json=tool_call_payload(tool, arguments), headers=mcp_headers()
    )
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["content"]
    assert backend.observed == []
    assert backend.payloads == []


def test_json_download_handoff() -> None:
    backend = FakeMCPBackend([])
    job_id = "table%ü"
    backend.observations[job_id] = result_observation(
        snapshot=backend.job_status(job_id, "succeeded", "metric"),
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": job_id,
                "outputs": {"one": [], "two": []},
                "output_metadata": {"one": {"columns": []}, "two": {"columns": []}},
            }
        ),
        lifetime=ResultLifetime(expires_in_seconds=60),
    )
    wire = _inspect(backend, job_id, tool="lyra_download_result")
    assert wire["isError"] is False
    payload = wire["structuredContent"]
    assert payload["format"] == "json"
    assert payload["media_type"] == "application/json"
    assert payload["lifetime"]["expires_in_seconds"] == 60
    assert payload["lyra_api"]["url"].endswith("table%25%C3%BC/result/download")
    assert payload["lyra_api"]["authentication"] == {
        "scheme": "Bearer",
        "credential_env_var": "LYRA_AGENT_API_KEY",
    }
    assert list(
        _inspect(backend, job_id)["structuredContent"]["result"]["outputs"]
    ) == [
        "one",
        "two",
    ]


def test_active_download_polling_guidance() -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        snapshot=backend.job_status("job-1", "queued", "metric")
    )
    payload = _inspect(backend, tool="lyra_download_result")["structuredContent"]
    assert payload["error"]["code"] == "result_not_ready"
    assert payload["error"]["details"]["poll_after_seconds"] == 2


def test_wide_multibyte_output_inspection_is_bounded() -> None:
    backend = FakeMCPBackend([])
    columns = [f"column_{i}" for i in range(40)]
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [
                        dict(zip(columns, row, strict=True))
                        for row in [["界" * 1000] * 40 for _ in range(30)]
                    ]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    wire = _inspect(backend)
    assert (
        len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())
        <= 65536
    )
    payload = wire["structuredContent"]["result"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )
    entry = payload["outputs"]["result"]
    assert entry["preview"] is None
    assert entry["truncation"]["omitted_rows"] == 30
    assert entry["truncation"]["omitted_columns"] == 20
    assert "preview" in entry["truncation"]["omitted_sections"]


def test_oversized_identifiers_and_errors_have_small_fallback() -> None:
    backend = FakeMCPBackend([])
    job_id = "x" * 40000
    backend.observations[job_id] = result_observation(
        snapshot=backend.job_status(job_id, "failed", "metric"),
        result=FailedJobResult(job_id=job_id, error={"message": "error"}),
    )
    for identifier in [job_id, "unknown" * 10000]:
        wire = _inspect(backend, identifier)
        assert wire["isError"] is True
        assert wire["structuredContent"]["error"]["code"] == "result_response_too_large"
        assert len(json.dumps(wire).encode()) < 65536


@pytest.mark.parametrize(
    "tool", ["lyra_run_metric", "lyra_get_job_result", "lyra_download_result"]
)
def test_operation_deadlines_and_cancellation(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FakeMCPBackend([_table_metric("metric", "metric")])
    cancelled = []

    async def blocked(*_args: object, **_kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    method = "create_job" if tool == "lyra_run_metric" else "observe_job"
    operation = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(backend, method, operation)
    arguments = (
        {
            "metric": "metric",
            "inputs": {
                "location": {
                    "collection": "test:units",
                    "unit_type": "lot",
                    "ids": ["09.01"],
                }
            },
            "parameters": {},
        }
        if tool == "lyra_run_metric"
        else {"job_id": "job-1"}
    )
    monkeypatch.setattr(tools, "OPERATION_TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(call_tool(backend, tool, arguments))
    assert result["isError"]
    assert result["structuredContent"]["error"]["code"] == "operation_timeout"
    assert operation.call_count == 1
    assert cancelled == [True]
    if tool == "lyra_run_metric":
        assert "duplicate" in str(result["structuredContent"]["error"]["details"])

    async def externally_cancel() -> None:
        monkeypatch.setattr(tools, "OPERATION_TIMEOUT_SECONDS", 30)
        started = asyncio.Event()

        async def cancellable(*args: object, **kwargs: object) -> None:
            started.set()
            await blocked(*args, **kwargs)

        operation.side_effect = cancellable
        task = asyncio.create_task(call_tool(backend, tool, arguments))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(externally_cancel())
    assert cancelled == [True, True]


def test_retryable_observation_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeMCPBackend([])
    failure = ToolCallError("backend_error", "unavailable", {"retryable": True})
    operation = AsyncMock(side_effect=failure)
    monkeypatch.setattr(backend, "observe_job", operation)
    result = asyncio.run(call_tool(backend, "lyra_get_job_result", {"job_id": "job-1"}))
    assert result["isError"]
    assert result["structuredContent"]["error"]["code"] == "backend_error"
    operation.assert_awaited_once()


def test_compact_provenance_contracts_and_progress_are_schema_valid() -> None:
    backend = FakeMCPBackend([])
    columns = [
        ColumnMetadata(
            name=f"v{i}",
            dtype="float64",
            unit=Unit.COUNT,
            description="x" * 600,
            nullable=False,
        )
        for i in range(25)
    ]
    provenance = result_provenance(
        {
            "metric": "metric",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "input": {
                "year": 2026,
                "zones": [1, 2, 3],
                "geometry": {
                    "data_type": "geojson",
                    "value": {
                        "type": "FeatureCollection",
                        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [99, 20]},
                            }
                        ]
                        * 1000,
                    },
                },
            },
            "outputs": {"result": OutputMetadata(kind="table", columns=columns)},
        }
    )
    unchanged = provenance.model_dump_json()
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        provenance=provenance,
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [
                        dict(zip([column.name for column in columns], row, strict=True))
                        for row in [[2.5] * 25]
                    ]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    payload = _inspect(backend)["structuredContent"]["result"]
    assert payload["provenance"]["input"]["parameters"] == {
        "year": 2026,
        "zones": {"type": "array", "count": 3},
        "geometry": {
            "type": "FeatureCollection",
            "feature_count": 1000,
            "crs": "EPSG:4326",
        },
    }
    assert "coordinates" not in json.dumps(payload)
    assert "output" not in payload["provenance"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )
    assert provenance.model_dump_json() == unchanged
    snapshot = backend.job_status("job-1", "running", "metric")
    snapshot.progress = JobProgress(
        timestamp=_COMPLETED_AT,
        stage="compute",
        current=1,
        total=10,
        message="x" * 1000,
    )
    backend.observations["job-1"] = JobObservation(snapshot=snapshot)
    payload = _inspect(backend)["structuredContent"]["result"]
    assert payload["progress"]["current"] == 1
    assert payload["progress"]["total"] == 10
    assert len(payload["progress"]["message"]) == 500
    assert payload["truncation"]["shortened_strings"] == 1


def test_large_errors_and_oversized_table_fields_are_omitted() -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "failed", "metric"),
        result=FailedJobResult(
            job_id="job-1",
            error={
                "code": "x" * 50000,
                "message": "界" * 1000,
                "details": ["large"] * 10000,
            },
        ),
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    assert wire["structuredContent"]["result"]["error"] is None
    assert (
        "error" in wire["structuredContent"]["result"]["truncation"]["omitted_sections"]
    )
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [
                        dict(zip(["x" * 1000], row, strict=True)) for row in [[2]]
                    ]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    payload = _inspect(backend)["structuredContent"]["result"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )
    entry = payload["outputs"]["result"]
    assert entry["preview"] == [{"x" * 1000: 2}]
    assert entry["metadata"]["summary"]["x" * 1000]["minimum"] == 2
    assert entry["truncation"]["omitted_columns"] == 0


def test_backend_errors_are_retryable_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeMCPBackend([])
    observe = AsyncMock(side_effect=RedisConnectionError("private backend location"))
    monkeypatch.setattr(backend, "observe_job", observe)
    wire = _inspect(backend)
    assert wire["isError"] is True
    assert wire["structuredContent"]["error"]["details"]["retryable"] is True
    assert "private backend" not in json.dumps(wire)
    observe.assert_awaited_once()


@pytest.mark.parametrize("value", [10**400, 1e308])
def test_large_numeric_values_remain_exact(value: float) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [
                        dict(zip(["value"], row, strict=True))
                        for row in [[value], [value]]
                    ]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    payload = wire["structuredContent"]["result"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )
    saved = backend.observations["job-1"].inspection
    assert saved is not None
    assert saved["result"]["preview"] == [{"value": value}, {"value": value}]
    entry = payload["outputs"]["result"]
    assert entry["preview"] == [{"value": value}, {"value": value}]
    assert entry["metadata"]["summary"]["value"]["minimum"] == value
    assert entry["metadata"]["summary"]["value"]["maximum"] == value


def test_output_names_are_never_silently_dropped() -> None:

    backend = FakeMCPBackend([])
    observation = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        result=TableJobResult(
            result_ref="a" * 32,
            execution_id="b" * 32,
            job_id="job-1",
            outputs={"result": []},
            output_metadata={"result": {"columns": []}},
        ),
    )
    backend.observations["job-1"] = replace(
        observation,
        inspection={
            name: {"metadata": None, "preview": None, "truncation": {}}
            for name in ["x" * 40000, "second"]
        },
    )
    wire = _inspect(backend)
    assert wire["isError"] is True
    assert wire["structuredContent"]["error"]["code"] == "result_response_too_large"


def test_file_results_are_not_in_public_schema() -> None:
    schema = json.dumps(advertised_tools()["lyra_get_job_result"].outputSchema)
    assert "file_path" not in schema
    assert "jsonl" not in schema


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
@pytest.mark.parametrize("job_id", ["", None, True, 42, 1.5, [], {}])
def test_job_id_validation(tool: str, job_id: object) -> None:
    backend = FakeMCPBackend([])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=tool_call_payload(tool, {"job_id": job_id}),
        headers=mcp_headers(),
    )
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["content"]
    assert backend.observed == []


def test_discovery_preserves_nullable_date_metadata() -> None:
    metric = _table_metric("area", "Area measurement")
    metric.outputs[metric.name] = OutputMetadata.model_validate(
        {
            "kind": "table",
            "columns": [
                {
                    "name": "area",
                    "dtype": "float64",
                    "unit": "m2",
                    "description": "Area",
                    "nullable": True,
                }
            ],
        }
    )
    backend = FakeMCPBackend([metric])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    for name, arguments in [
        ("lyra_get_metric", {"metric": "area"}),
    ]:
        response = client.post(
            "/", json=tool_call_payload(name, arguments), headers=mcp_headers()
        )
        assert response.json()["result"]["isError"] is False
        payload = tool_payload(response)
        validate(payload, advertised_tools()[name].outputSchema)
        columns = (
            payload["outputs"][metric.name]["columns"]
            if name == "lyra_get_metric"
            else payload["candidates"][0]["relevant_columns"]
        )
        assert columns[0]["nullable"] is True
    provenance = result_provenance(
        {
            "metric": "area",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "input": {},
            "outputs": {"result": metric.outputs[metric.name]},
        }
    )
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        provenance=provenance,
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [dict(zip(["area"], row, strict=True)) for row in [[10]]]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    payload = _inspect(backend)["structuredContent"]["result"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )
    assert (
        provenance.accepted.plan.output_metadata["result"]
        == metric.outputs[metric.name]
    )


def test_mcp_parameterless_metric_preserves_input() -> None:
    backend = FakeMCPBackend([_parameterless_metric("report", "Write a report.")])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    payload = {
        "location": {"collection": "test:units", "unit_type": "lot", "ids": ["09.01"]}
    }
    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "report",
                "inputs": {k: v for k, v in payload.items() if k != "parameters"},
                "parameters": payload.get("parameters", {}),
            },
        ),
        headers=mcp_headers(),
    )
    assert response.json()["result"].get("isError", False) is False
    assert backend.payloads == [
        {**payload, "parameters": payload.get("parameters", {})}
    ]


@pytest.mark.parametrize("parameters", [{}, {"location": "an ordinary parameter"}])
def test_mcp_keeps_declared_parameters_nested(parameters: dict[str, Any]) -> None:
    metric = _table_metric("nested_metric", "Return a table.")
    metric.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"location": {"type": "string"}},
    }
    backend = FakeMCPBackend([metric])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=tool_call_payload(
            "lyra_run_metric",
            {
                "metric": "nested_metric",
                "inputs": {
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["09.01"],
                    }
                },
                "parameters": parameters,
            },
        ),
        headers=mcp_headers(),
    )
    assert response.json()["result"].get("isError", False) is False
    assert backend.payloads == [
        {
            "location": {
                "collection": "test:units",
                "unit_type": "lot",
                "ids": ["09.01"],
            },
            "parameters": parameters,
        }
    ]


@pytest.mark.parametrize("unit", [Unit.COUNT, None])
def test_mcp_discovery_and_result_preserve_canonical_or_null_units(
    unit: Unit | None,
) -> None:
    metric = _table_metric("quantity", "Test quantity")
    metric.outputs[metric.name] = OutputMetadata(
        columns=[
            ColumnMetadata(
                name="value",
                dtype="int64",
                unit=unit,
                description="Test quantity.",
            )
        ]
    )
    backend = FakeMCPBackend([metric])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    for name, arguments in [
        ("lyra_get_metric", {"metric": "quantity"}),
    ]:
        response = client.post(
            "/",
            json=tool_call_payload(name, arguments),
            headers=mcp_headers(),
        )
        payload = tool_payload(response)
        validate(payload, advertised_tools()[name].outputSchema)
        columns = (
            payload["outputs"][metric.name]["columns"]
            if name == "lyra_get_metric"
            else payload["candidates"][0]["relevant_columns"]
        )
        assert columns[0]["unit"] == unit
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", "succeeded", "metric"),
        provenance=result_provenance(
            {
                "metric": "quantity",
                "catalog_fingerprint": "catalog",
                "plugin": {"name": "plugin", "version": "1"},
                "created_at": _COMPLETED_AT,
                "input": {},
                "outputs": {"result": metric.outputs[metric.name]},
            }
        ),
        result=TableJobResult.model_validate(
            {
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "job_id": "job-1",
                "outputs": {
                    "result": [dict(zip(["value"], row, strict=True)) for row in [[2]]]
                },
                "output_metadata": {"result": {"columns": []}},
            }
        ),
    )
    payload = _inspect(backend)["structuredContent"]["result"]
    assert list(payload["outputs"]) == (
        ["result"] if payload["status"] == "succeeded" else []
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"metric": None},
        {"metric": 1, "inputs": {}, "parameters": {}},
        {
            "metric": "report",
            "inputs": [],
            "parameters": {},
        },
        {"metric": "report", "inputs": {}, "parameters": {}, "extra": True},
    ],
)
def test_submission_envelope_validation(arguments: dict[str, Any]) -> None:
    schema = advertised_tools()["lyra_run_metric"].inputSchema
    assert (
        set(schema["properties"])
        == set(JobCreateRequest.model_fields)
        == {"metric", "inputs", "parameters", "interventions"}
    )
    assert schema["required"] == ["metric"]
    backend = FakeMCPBackend([])
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/",
        json=tool_call_payload("lyra_run_metric", arguments),
        headers=mcp_headers(),
    )
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["content"]
    assert backend.payloads == []


def _submission_fixture(
    tmp_path: Path, scenario: str
) -> tuple[LyraConfig, dict[str, Any]]:
    spatial = {"location", "bounds"} if scenario == "both" else {"location"}
    declaration = metric_manifest(name="report", spatial=spatial)
    if scenario == "private_root":
        declaration["private"] = True
    payload: dict[str, Any] = {
        "parameters": {"value": 7},
        **{field: selection("area-1").model_dump(mode="json") for field in spatial},
    }
    if scenario in {"empty", "extra_parameters", "omitted"}:
        declaration["location_inputs"] = {}
        declaration["parameters_schema"] = None
        payload = (
            {}
            if scenario in {"empty", "omitted"}
            else {"parameters": {"unexpected": 1}}
        )
    elif scenario in {"defaults", "nested"}:
        declaration["parameters_schema"] = metric_manifest(
            parameters=NestedDefaultParameters
        )["parameters_schema"]
        payload["parameters"] = (
            {}
            if scenario == "defaults"
            else {"nested": [None, {"values": [1, True, "text"]}]}
        )
    elif scenario == "missing_parameters":
        payload["parameters"] = {}
    elif scenario == "missing_inputs":
        payload = {"parameters": {"value": 7}}
    elif scenario == "invalid":
        payload["parameters"] = {"value": "invalid"}
    elif scenario == "malformed":
        payload["location"]["ids"] = [""]
    manifest = plugin_manifest(declaration)
    if scenario != "missing_provider":
        manifest["providers"] = [SELECTED.declaration.model_dump(mode="json")]
        manifest["resources"] = {SELECTED.declaration.resource: {}}
    (tmp_path / "lyra.plugin.json").write_text(json.dumps(manifest))
    config = load_test_config(tmp_path, plugins=[tmp_path])
    config.mcp.enabled = True
    return config, payload


@pytest.mark.parametrize(
    "scenario",
    [
        "empty",
        "selection",
        "both",
        "selector",
        "invalid",
        "malformed",
        "missing_provider",
        "unknown",
        "private_root",
        "extra_parameters",
        "defaults",
        "nested",
        "missing_parameters",
        "missing_inputs",
        "null_metric",
        "null_inputs",
        "null_parameters",
        "unknown_field",
        "string_inputs",
        "string_parameters",
        "numeric_metric",
        "omitted",
        "edition",
        "unknown_edition",
        "revision",
        "null_revision",
        "blank_revision",
        "long_revision",
        "numeric_revision",
        "numeric_edition",
    ],
)
def test_mounted_submission_matches_rest(
    tmp_path: Path,
    scenario: str,
) -> None:
    config, payload = _submission_fixture(tmp_path, scenario)
    app = main.create_app(config, credentials=api_credentials())
    enqueue = Mock()
    request: JsonObject = {
        "metric": "missing" if scenario == "unknown" else "report",
        "inputs": {k: v for k, v in payload.items() if k != "parameters"},
        "parameters": payload.get("parameters", {}),
    }
    if scenario.startswith("null_"):
        request[scenario.removeprefix("null_")] = None
    elif scenario == "unknown_field":
        request["unexpected"] = True
    elif scenario.startswith("string_"):
        request[scenario.removeprefix("string_")] = "{}"
    elif scenario == "numeric_metric":
        request["metric"] = 42
    elif scenario == "omitted":
        request = {"metric": "report"}
    options: dict[str, JsonObject] = {
        "edition": {"edition": "release"},
        "unknown_edition": {"edition": "unknown"},
        "numeric_edition": {"edition": 42},
        "revision": {"cache_revision": " revision-λ "},
        "null_revision": {"cache_revision": None},
        "blank_revision": {"cache_revision": " "},
        "long_revision": {"cache_revision": "x" * 257},
        "numeric_revision": {"cache_revision": 42},
    }
    request.update(options.get(scenario, {}))
    original = deepcopy(request)

    rest, wire = asyncio.run(submit_both(app, request, enqueue))
    result = wire.get("structuredContent", {})
    errors = {
        "invalid": "invalid_parameters",
        "malformed": "invalid_parameters",
        "extra_parameters": "invalid_parameters",
        "unknown": "unknown_metric",
        "private_root": "invalid_parameters",
        "missing_parameters": "invalid_parameters",
        "missing_inputs": "invalid_parameters",
        "null_metric": "invalid_arguments",
        "null_inputs": "invalid_arguments",
        "null_parameters": "invalid_arguments",
        "unknown_field": "invalid_arguments",
        "string_inputs": "invalid_arguments",
        "string_parameters": "invalid_arguments",
        "numeric_metric": "invalid_arguments",
        "unknown_edition": "invalid_arguments",
        "numeric_edition": "invalid_arguments",
        "edition": "invalid_arguments",
        "revision": "invalid_arguments",
        "null_revision": "invalid_arguments",
        "blank_revision": "invalid_arguments",
        "long_revision": "invalid_arguments",
        "numeric_revision": "invalid_arguments",
    }
    assert request == original
    if scenario in errors:
        assert rest.status_code in {404, 422, 503}
        assert wire["isError"] is True
        if errors[scenario] != "invalid_arguments":
            assert result["error"]["code"] == errors[scenario]
        if errors[scenario] == "invalid_parameters":
            assert result["error"]["details"] == rest.json()["detail"]
        enqueue.assert_not_called()
    else:
        assert rest.status_code == 202
        assert result["next_tool"] == "lyra_get_job_result"
        assert enqueue.call_count == 2
        first, second = [call.args for call in enqueue.call_args_list]
        assert first[0].job_id != second[0].job_id
        assert submitted_input(first[0]) == submitted_input(second[0])
        plan = first[0].accepted.plan
        assert plan.request.model_dump(mode="json")["inputs"] == original.get(
            "inputs", {}
        )
        assert plan.request.parameters == plan.parameters
        assert plan.node == "report"
        if scenario in {"defaults", "nested"}:
            assert plan.parameters == {
                "value": 7,
                "nested": None,
                **payload["parameters"],
            }
        assert first[0].accepted.model_dump(exclude={"created_at"}) == second[
            0
        ].accepted.model_dump(exclude={"created_at"})


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_result_identifies_accepted_edition(status: JobLifecycleStatus) -> None:
    backend = FakeMCPBackend([])
    provenance = result_provenance(
        {
            "metric": "quantity",
            "catalog_fingerprint": "catalog",
            "plugin": {"name": "plugin", "version": "1"},
            "created_at": _COMPLETED_AT,
            "input": {},
            "outputs": {"result": {"columns": []}},
        }
    )
    result = (
        TableJobResult(
            result_ref="a" * 32,
            execution_id="b" * 32,
            job_id="job-1",
            outputs={"result": []},
            output_metadata=provenance.accepted.plan.output_metadata,
            provenance=provenance,
        )
        if status == "succeeded"
        else None
    )
    backend.observations["job-1"] = result_observation(
        snapshot=backend.job_status("job-1", status, "concrete-node"),
        provenance=provenance,
        result=result,
    )
    wire = _inspect(backend)
    assert wire["isError"] is False
    payload = wire["structuredContent"]["result"]
    assert payload["metric"] == "quantity"
    if status in {"succeeded", "failed"}:
        assert "cache_revision" not in payload["provenance"]


@pytest.mark.parametrize(
    "job_id", [" padded ", " ", "λ/结果?#", "lyra://results/job-1", "null", "{}", "[]"]
)
def test_mcp_job_workflow_preserves_exact_identity(
    job_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FakeMCPBackend([])
    submit = AsyncMock(
        return_value=JobCreateResponse(
            job_id=job_id,
            metric="metric",
            status="queued",
            links=JobLinks(self="/jobs/job", result="/jobs/job/result"),
        )
    )
    monkeypatch.setattr(backend, "create_job", submit)
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    submitted = tool_payload(
        client.post(
            "/",
            json=tool_call_payload("lyra_run_metric", {"metric": "metric"}),
            headers=mcp_headers(),
        )
    )
    assert submitted["job_id"] == job_id
    for status in ("queued", "running", "succeeded"):
        snapshot = backend.job_status(job_id, status, "metric")
        backend.observations[job_id] = (
            result_observation(
                snapshot=snapshot,
                result=TableJobResult(
                    result_ref="a" * 32,
                    execution_id="b" * 32,
                    job_id=job_id,
                    outputs={"named": []},
                    output_metadata={"named": {"columns": []}},
                ),
            )
            if status == "succeeded"
            else JobObservation(snapshot=snapshot)
        )
        for _ in range(2):
            wire = _inspect(backend, submitted["job_id"])
            assert wire["isError"] is False
            payload = wire["structuredContent"]["result"]
            assert payload["job_id"] == job_id
            assert payload["status"] == status
        download = _inspect(backend, submitted["job_id"], tool="lyra_download_result")
        if status == "succeeded":
            assert list(payload["outputs"]) == ["named"]
            path = f"/jobs/{quote(job_id, safe='-._~')}/result/download"
            assert payload["download"] == path
            handoff = download["structuredContent"]
            assert handoff["job_id"] == job_id
            assert handoff["lyra_api"]["url"] == "https://lyra.example.test/api" + path
            assert handoff["lyra_api"]["authentication"]["scheme"] == "Bearer"
        else:
            assert download["structuredContent"]["error"]["code"] == "result_not_ready"
    assert backend.observed == [job_id] * 9
    submit.assert_awaited_once()


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
def test_uri_shaped_job_id_is_looked_up_literally(tool: str) -> None:
    backend = FakeMCPBackend([])
    backend.observations["job-1"] = JobObservation(
        snapshot=backend.job_status("job-1", "queued", "metric")
    )
    wire = _inspect(backend, "lyra://results/job-1", tool=tool)
    assert wire["structuredContent"]["error"]["code"] == "result_not_found"
    assert backend.observed == ["lyra://results/job-1"]


@pytest.mark.parametrize("tool", ["lyra_get_job_result", "lyra_download_result"])
def test_domain_integrity_failure_is_a_tool_error(tool: str) -> None:
    observe = AsyncMock(
        side_effect=InvalidJobResultError("Missing required acceptance.")
    )
    runtime = Mock(results=Mock(observe_job=observe))
    backend = InProcessLyraBackend(lambda: runtime)
    client = managed_client(
        create_mcp_app(agent_api_key="agent-secret", backend=backend)
    )
    response = client.post(
        "/", json=tool_call_payload(tool, {"job_id": "damaged"}), headers=mcp_headers()
    )
    assert response.json()["result"]["isError"] is True
    assert tool_payload(response)["error"]["code"] == "result_observation_error"
    observe.assert_awaited_once_with("damaged")


class RetainedBackend(FakeMCPBackend):
    """Keep the same discovery object to expose accidental retained mutations."""

    discovery: Discovery

    async def get_discovery(self) -> Discovery:
        return self.discovery


def test_direct_operations_and_mutation_isolation() -> None:
    async def exercise() -> None:
        metric = MetricManifest.model_validate(
            metric_manifest(name="report", spatial=set())
        )
        backend = RetainedBackend([metric])
        backend.discovery = await FakeMCPBackend.get_discovery(backend)
        listed = await tools.list_metrics(ListMetricsInput(limit=1), backend)
        searched = await tools.search_metrics(
            SearchMetricsInput(query="report"), backend
        )
        inspected = await tools.get_metric(GetMetricInput(metric="report"), backend)
        dependencies = await tools.get_metric_dependencies(
            GetMetricDependenciesInput(metric="report"), backend
        )
        assert listed.metrics[0].name == searched.candidates[0].name == "report"
        assert inspected.metric.name == dependencies.metric.name == "report"
        assert dependencies.relationships == []
        listed.metrics[0].output_granularity.clear()
        searched.candidates[0].description = "changed"
        inspected.metric.output_granularity.clear()
        again = await tools.get_metric(GetMetricInput(metric="report"), backend)
        assert again.metric.output_granularity
        assert again.metric.description != "changed"

    asyncio.run(exercise())


def test_direct_job_operations_and_mutation_isolation() -> None:
    async def exercise() -> None:
        backend = FakeMCPBackend(
            [
                MetricManifest.model_validate(
                    metric_manifest(name="report", spatial=set())
                )
            ]
        )
        request = JobCreateRequest(metric="report")
        first = await tools.run_metric(request, backend)
        second = await tools.run_metric(request, backend)
        assert first.job_id != second.job_id
        assert len(backend.payloads) == 2
        job_id = first.job_id
        backend.observations[job_id] = result_observation(
            snapshot=backend.job_status(job_id, "succeeded", "report"),
            result=TableJobResult(
                result_ref="a" * 32,
                execution_id="b" * 32,
                job_id=job_id,
                outputs={"named": [{"value": 3}]},
                output_metadata={"named": {"columns": []}},
            ),
        )
        before = backend.observations[job_id]
        result = await tools.get_job_result(GetJobResultInput(job_id=job_id), backend)
        assert isinstance(result, TerminalResultOutput)
        original = result.model_dump(mode="json")
        result.lifetime.expires_in_seconds = 0
        result.outputs.clear()
        download = await tools.download_result(
            JobIdInput(job_id=job_id),
            backend,
            public_api_base_url="https://example.test",
        )
        assert download.lyra_api.url.endswith(f"/jobs/{job_id}/result/download")
        download.lifetime.expires_in_seconds = 0
        repeated = await tools.get_job_result(GetJobResultInput(job_id=job_id), backend)
        assert repeated.model_dump(mode="json") == original
        assert backend.observations[job_id] is before
        assert len(backend.payloads) == 2
        backend.observations[job_id] = JobObservation(
            snapshot=backend.job_status(job_id, "failed", "report")
        )
        failed = await tools.get_job_result(GetJobResultInput(job_id=job_id), backend)
        assert failed.status == "failed"

    asyncio.run(exercise())


class InterruptedBackend(FakeMCPBackend):
    failure: Exception | None = None
    calls = 0
    cleaned = 0

    async def _interrupt(self) -> None:
        self.calls += 1
        try:
            if self.failure is not None:
                raise self.failure
            await asyncio.Event().wait()
        finally:
            self.cleaned += 1

    async def create_job(self, request: JobCreateRequest) -> JobCreateResponse:
        await self._interrupt()
        return await super().create_job(request)

    async def observe_job(self, job_id: str) -> JobObservation | None:
        await self._interrupt()
        return await super().observe_job(job_id)


@pytest.mark.parametrize("operation", ["submit", "observe", "download"])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "redis", "os"])
def test_direct_operation_failure_boundaries(
    operation: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = InterruptedBackend([])
    if failure == "redis":
        backend.failure = RedisConnectionError("private address")
    elif failure == "os":
        backend.failure = OSError("private address")
    monkeypatch.setattr(
        tools, "OPERATION_TIMEOUT_SECONDS", 0.001 if failure == "timeout" else 30
    )

    async def invoke() -> None:
        if operation == "submit":
            await tools.run_metric(JobCreateRequest(metric="report"), backend)
        elif operation == "observe":
            await tools.get_job_result(GetJobResultInput(job_id="job"), backend)
        else:
            await tools.download_result(
                JobIdInput(job_id="job"),
                backend,
                public_api_base_url="https://example.test",
            )

    async def exercise() -> None:
        if failure == "cancel":
            task = asyncio.create_task(invoke())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(tools.ToolCallError) as caught:
                await invoke()
            assert caught.value.code == (
                "operation_timeout" if failure == "timeout" else "backend_error"
            )
            if operation == "submit":
                assert "duplicate" in str(caught.value.to_payload())
            assert "private address" not in str(caught.value.to_payload())
        assert backend.calls == backend.cleaned == 1
        assert backend.payloads == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "arguments",
    [
        {"job_id": "job-1", "unexpected": "界" * 40000},
        {"job_id": "job-1", **{f"extra_{index}": True for index in range(2000)}},
    ],
)
def test_inspection_validation_errors_obey_wire_budget(
    arguments: dict[str, Any],
) -> None:
    backend = FakeMCPBackend([])
    wire = asyncio.run(call_tool(backend, "lyra_get_job_result", arguments))
    assert wire["isError"] is True
    assert (
        len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())
        <= 65536
    )
    assert backend.observed == []


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("scenario", "sections"),
    [
        ("empty", {}),
        ("empty", {"inputs": None, "parameters": None}),
        ("empty", {"inputs": {}, "parameters": {}}),
        ("defaults", {}),
        ("defaults", {"parameters": None}),
        ("defaults", {"parameters": {"nested": [None, {"values": [1, True, "text"]}]}}),
    ],
)
def test_python_submission_reaches_shared_acceptance(
    scenario: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sections: dict[str, Any],
    *,
    asynchronous: bool,
) -> None:
    config, _ = _submission_fixture(tmp_path, scenario)
    if scenario == "defaults":
        sections = {
            **sections,
            "inputs": {"location": selection("area-1").model_dump(mode="json")},
        }
    app = main.create_app(config, credentials=api_credentials())
    native_sync, native_async = httpx.Client, httpx.AsyncClient
    bodies: list[dict[str, Any]] = []

    async def exercise() -> None:
        async with (
            app.router.lifespan_context(app),
            native_async(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
        ):
            enqueue = Mock()
            app.state.runtime.jobs.enqueue = enqueue
            loop = asyncio.get_running_loop()

            async def forward(request: httpx.Request) -> httpx.Response:
                bodies.append(json.loads(request.content))
                return await http.send(request)

            def forward_sync(request: httpx.Request) -> httpx.Response:
                response = asyncio.run_coroutine_threadsafe(
                    forward(request), loop
                ).result(10)
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=response.content,
                )

            monkeypatch.setattr(
                httpx,
                "Client",
                partial(native_sync, transport=httpx.MockTransport(forward_sync)),
            )
            monkeypatch.setattr(
                httpx,
                "AsyncClient",
                partial(native_async, transport=httpx.MockTransport(forward)),
            )

            def submit_sync() -> JobCreateResponse:
                with LyraClient(
                    "testserver", secure=False, agent_api_key="agent-secret"
                ) as client:
                    return client.jobs.create("report", **sections)

            if asynchronous:
                async with AsyncLyraClient(
                    "testserver", secure=False, agent_api_key="agent-secret"
                ) as client:
                    submitted = await client.jobs.create("report", **sections)
            else:
                submitted = await asyncio.to_thread(submit_sync)
            assert bodies == [
                {
                    "metric": "report",
                    "inputs": sections.get("inputs") or {},
                    "parameters": sections.get("parameters") or {},
                }
            ]
            assert enqueue.call_count == 1
            accepted = enqueue.call_args.args[0]
            assert submitted.job_id == accepted.job_id
            assert accepted.accepted.plan.request.model_dump(mode="json")["inputs"] == (
                sections.get("inputs") or {}
            )
            assert accepted.accepted.plan.parameters == (
                {"value": 7, "nested": None, **(sections.get("parameters") or {})}
                if scenario == "defaults"
                else {}
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("terminal", [JobStatus.FINISHED, JobStatus.FAILED])
def test_official_client_mounted_job_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: JobStatus
) -> None:
    config, _ = _submission_fixture(tmp_path, "empty")
    config.api.public_base_url = "https://lyra.example.test"
    app = main.create_app(config, credentials=api_credentials())

    async def exercise() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=mcp_headers(),
            ) as http,
            streamable_http_client("http://testserver/mcp/", http_client=http) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            definitions = {
                tool.name: tool for tool in (await session.list_tools()).tools
            }

            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                definition = definitions[name]
                validate(arguments, definition.inputSchema)
                result = await session.call_tool(name, arguments)
                assert not result.isError
                validate(result.structuredContent, definition.outputSchema)
                assert result.structuredContent is not None
                assert result.content[0].type == "text"
                assert json.loads(result.content[0].text) == result.structuredContent
                assert len(result.model_dump_json(by_alias=True).encode()) <= 64 * 1024
                return result.structuredContent

            for name, arguments in (
                ("lyra_list_metrics", {}),
                ("lyra_search_metrics", {"query": "report"}),
                ("lyra_get_metric", {"metric": "report"}),
                ("lyra_get_metric_dependencies", {"metric": "report"}),
            ):
                await call(name, arguments)
            enqueue = Mock()
            runtime = app.state.runtime
            runtime.jobs.enqueue = enqueue
            submitted = await call("lyra_run_metric", {"metric": "report"})
            envelope = enqueue.call_args.args[0]
            job_id = submitted["job_id"]
            assert job_id == envelope.job_id
            payload = {
                "job_id": job_id,
                "execution_id": "b" * 32,
                "result_ref": "a" * 32,
                "outputs": {"report": []},
                "output_metadata": {
                    name: metadata.model_dump(mode="json")
                    for name, metadata in envelope.accepted.plan.output_metadata.items()
                },
                "provenance": {"accepted": envelope.accepted.model_dump(mode="json")},
            }
            if terminal == JobStatus.FAILED:
                payload = {
                    "kind": "failed",
                    "job_id": job_id,
                    "error": {"message": "calculation failed"},
                    "provenance": payload["provenance"],
                }
            job = retain(
                runtime.results.result_store_path,
                monkeypatch,
                payload,
                jobs=runtime.jobs,
                status=terminal,
            )
            job.id = job_id
            job.args = (envelope.model_dump(mode="json"),)
            for state, expected in (
                (JobStatus.QUEUED, "queued"),
                (JobStatus.STARTED, "running"),
                (terminal, "succeeded" if terminal == JobStatus.FINISHED else "failed"),
            ):
                job.get_status.return_value = state
                inspection = await call("lyra_get_job_result", {"job_id": job_id})
                assert inspection["result"]["status"] == expected
                response = await http.get(f"/jobs/{job_id}")
                assert response.status_code == 200
                assert response.json()["status"] == expected
                for field, value in (
                    ("execution_id", "b" * 32),
                    ("result_ref", "a" * 32),
                ):
                    identity = value if expected == "succeeded" else None
                    assert response.json()[field] == identity
                    assert inspection["result"].get(field) == identity
            await _check_workflow_download(session, http, job_id, terminal)
            assert enqueue.call_count == 1

    asyncio.run(exercise())


async def _check_workflow_download(
    session: ClientSession, http: httpx.AsyncClient, job_id: str, terminal: JobStatus
) -> None:
    if terminal == JobStatus.FAILED:
        unavailable = await session.call_tool(
            "lyra_download_result", {"job_id": job_id}
        )
        assert unavailable.isError
        assert unavailable.structuredContent is not None
        assert (
            unavailable.structuredContent["error"]["code"] == "result_not_downloadable"
        )
        return
    download = await session.call_tool("lyra_download_result", {"job_id": job_id})
    assert not download.isError
    descriptor = download.structuredContent
    assert descriptor is not None
    validate(
        descriptor,
        next(
            tool.outputSchema
            for tool in (await session.list_tools()).tools
            if tool.name == "lyra_download_result"
        ),
    )
    url = descriptor["lyra_api"]["url"]
    denied = await http.get(url, headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 403
    downloaded = await http.get(url)
    assert downloaded.status_code == 200
    assert downloaded.json()["job_id"] == job_id
    assert downloaded.json()["status"] == "succeeded"
    assert downloaded.json()["execution_id"] == "b" * 32
    assert downloaded.json()["result_ref"] == "a" * 32
