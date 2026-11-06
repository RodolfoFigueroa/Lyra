import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, NotRequired, Self, TypedDict, TypeVar, Unpack, cast

import aiohttp
import pytest

from lyra.api.client.bridge import TransportBridge
from lyra.api.client.http import HTTPTransport
from lyra.contracts.config import DEFAULT_API_HOST
from lyra.contracts.jobs import (
    TableJobResult,
)
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.contracts.types import JsonValue


class _FakeSyncResponseOptions(TypedDict):
    status_code: NotRequired[int]
    payload: NotRequired[JsonValue]
    text: NotRequired[str]
    headers: NotRequired[dict[str, str] | None]
    lines: NotRequired[list[str] | None]
    chunks: NotRequired[list[bytes] | None]


class SyncRequestOptions(TypedDict):
    params: dict[str, Any] | None
    json: dict[str, Any] | None
    timeout: float
    headers: dict[str, str]


class FakeSyncResponse:
    def __init__(self, **options: Unpack[_FakeSyncResponseOptions]) -> None:
        self.status_code = options.get("status_code", 200)
        self._payload = options.get("payload")
        self.text = options.get("text", json.dumps(self._payload))
        self.headers = options.get("headers") or {"content-type": "application/json"}
        self._lines = options.get("lines") or []
        self.chunks = options.get("chunks") or []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def json(self) -> JsonValue:
        assert self._payload is not None
        return self._payload

    def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:  # ruff:ignore[unused-method-argument]
        yield from self._lines

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:  # ruff:ignore[unused-method-argument]
        yield from self.chunks


def install_sync_handler(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[..., FakeSyncResponse]
) -> None:
    class Session(FakeSession):
        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            assert isinstance(self, Session)
            kwargs["timeout"] = cast("aiohttp.ClientTimeout", kwargs["timeout"]).total
            response = handler(*args, **kwargs)
            return FakeAsyncResponse(
                status=response.status_code,
                text=response.text,
                headers=response.headers,
                chunks=response.chunks,
            )

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            kwargs["stream"] = True
            return self.request("GET", *args, **kwargs)

    monkeypatch.setattr(aiohttp, "ClientSession", Session)


def _mock_sync_http(
    monkeypatch: pytest.MonkeyPatch, **handlers: Callable[..., FakeSyncResponse]
) -> None:
    previous = getattr(aiohttp.ClientSession, "handlers", {})
    merged = previous | handlers

    def request(method: str, url: str, **kwargs: object) -> FakeSyncResponse:
        options = {key: value for key, value in kwargs.items() if value is not None}
        return merged[method.lower()](url, **options)

    install_sync_handler(monkeypatch, request)
    monkeypatch.setattr(aiohttp.ClientSession, "handlers", merged, raising=False)


def _job_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "links": {
            "self": "/jobs/job-1",
            "result": "/jobs/job-1/result",
        },
    }


def _status_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _job_list_response() -> dict[str, Any]:
    return {
        "jobs": [
            {
                "job_id": "job-1",
                "metric": "heavy_metric",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    }


def _job_detail_response() -> dict[str, Any]:
    return {
        "snapshot": _status_response(),
        "queue": "heavy",
        "worker_id": "worker-1",
        "failure_diagnostics": None,
    }


def _liveness_response() -> dict[str, Any]:
    return {
        "status": "ok",
        "api_version": "0.1.0",
    }


def _readiness_response() -> dict[str, Any]:
    return {
        "status": "ready",
        "api_version": "0.1.0",
        "redis": {"status": "ok"},
    }


def _redis_unavailable_response() -> dict[str, Any]:
    return {
        "detail": {
            "code": "redis_unavailable",
            "message": "Redis is temporarily unavailable.",
            "retryable": True,
        }
    }


def _admin_status_response() -> dict[str, Any]:
    return {
        "api_version": "0.1.0",
        "redis": {"status": "ok"},
        "metric_count": 1,
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "configured_worker_count": 1,
        "result_retention_seconds": 86400,
        "catalog_fingerprint": "abc",
    }


def _config_summary_response() -> dict[str, Any]:
    return {
        "api_host": DEFAULT_API_HOST,
        "api_port": 5219,
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "workers": [
            {
                "name": "interactive",
                "queues": ["interactive"],
                "concurrency": 1,
                "temp_dir": "/lyra_data/cache/jobs/interactive",
            }
        ],
        "result_retention_seconds": 86400,
    }


def _catalog_summary_response() -> dict[str, Any]:
    return {
        "metric_count": 1,
        "metric_names": ["smoke_table_metric"],
        "catalog_fingerprint": "abc",
        "installed_plugins": [
            {
                "distribution": "lyra-smoke-plugin",
                "version": "0.1.0",
                "enabled": True,
            }
        ],
        "metric_queues": {"smoke_table_metric": "interactive"},
    }


def _workers_response() -> dict[str, Any]:
    return {
        "pools": [],
        "workers": [
            {
                "name": "interactive",
                "hostname": "host",
                "queues": ["interactive"],
                "state": "busy",
                "stale": False,
                "current_job": "job-1",
                "heartbeat_age_seconds": 0.25,
            }
        ],
    }


def _worker_detail_response() -> dict[str, Any]:
    return _workers_response()["workers"][0]


def _queues_response() -> dict[str, Any]:
    return {
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "queues": [
            {
                "name": "interactive",
                "is_default": True,
                "assigned_metric_count": 1,
                "configured_workers": ["interactive"],
                "observed_workers": ["interactive"],
                "pending_depth": None,
                "pending_depth_unknown": True,
            }
        ],
    }


def _plugin_repo_response() -> dict[str, Any]:
    return {
        "distribution": "lyra-smoke-plugin",
        "version": "0.1.0",
        "enabled": True,
    }


def _plugin_repo_list_response() -> dict[str, Any]:
    return {"plugins": [_plugin_repo_response()]}


def _plugin_routing_response() -> dict[str, Any]:
    return {
        "metric_queues": {"smoke_table_metric": "interactive"},
        "allowed_queues": ["interactive", "batch"],
        "default_queue": "interactive",
    }


def _result_response() -> dict[str, Any]:
    return {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "outputs": {
            "result": [dict(zip(["value"], row, strict=True)) for row in [[6]]]
        },
        "output_metadata": {"result": {"columns": []}},
    }


def _result_descriptor_response() -> dict[str, Any]:
    result = TableJobResult.model_validate(_result_response())
    result.output_metadata["result"] = OutputMetadata(
        columns=[
            ColumnMetadata(
                name="value",
                dtype="int64",
                unit="count",
                description="Example output value.",
            )
        ]
    )
    provenance = {
        "metric": "heavy_metric",
        "catalog_fingerprint": "catalog-1",
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "input": {},
        "outputs": result.output_metadata,
        "created_at": "2026-07-09T12:00:00Z",
    }
    # Basic transport fixtures must also import with runtime/native code blocked.
    from tests.provenance_helpers import (  # ruff: ignore[import-outside-top-level] - keep transport fixtures lightweight
        result_provenance,
    )
    from tests.result_helpers import (  # ruff: ignore[import-outside-top-level] - keep transport fixtures lightweight
        inspection_of,
    )

    return inspection_of(
        result,
        completed_at=datetime.fromisoformat("2026-07-09T12:05:00+00:00"),
        provenance=result_provenance(provenance),
    ).model_dump(mode="json")


def _data_types_response() -> dict[str, Any]:
    return {"location_selection_schema": {"type": "object"}}


def _family_response() -> dict[str, Any]:
    return {
        "name": "accessibility_by_destination",
        "description": "Compute accessibility by destination.",
        "output_granularity": {},
        "requires_edition": False,
        "editions": [{"edition": None, "accepted_selections": {}}],
    }


def _metric_response() -> dict[str, Any]:
    return {
        "family": _family_response(),
        "edition": None,
        "node": "accessibility_by_destination",
        "edition_metadata": {},
        "native_inputs": {},
        "parameters": {},
        "bindings": {},
        "accepted_selections": {},
        "outputs": {
            "accessibility_by_destination": {
                "kind": "table",
                "columns": [
                    {
                        "name": "job_accessibility",
                        "dtype": "float64",
                        "unit": "count",
                        "description": "Job accessibility.",
                        "nullable": False,
                    }
                ],
            }
        },
    }


def _metric_catalog_response() -> JsonValue:
    return [_family_response()]


class FakeContent:
    def __init__(
        self,
        *,
        lines: list[str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.lines = lines or []
        self.chunks = chunks or []

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter_lines()

    async def _iter_lines(self) -> AsyncIterator[bytes]:
        for line in self.lines:
            yield f"{line}\n".encode()

    async def iter_chunked(
        self,
        chunk_size: int,  # ruff:ignore[unused-method-argument]
    ) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class _FakeAsyncResponseOptions(TypedDict):
    status: NotRequired[int]
    payload: NotRequired[JsonValue]
    text: NotRequired[str]
    headers: NotRequired[dict[str, str] | None]
    lines: NotRequired[list[str] | None]
    chunks: NotRequired[list[bytes] | None]


class FakeAsyncResponse:
    def __init__(self, **options: Unpack[_FakeAsyncResponseOptions]) -> None:
        self.status = options.get("status", 200)
        self._payload = options.get("payload")
        self._text = options.get("text", json.dumps(self._payload))
        self.headers = options.get("headers") or {"content-type": "application/json"}
        self.content = FakeContent(
            lines=options.get("lines"),
            chunks=options.get("chunks"),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> JsonValue:
        assert self._payload is not None
        return self._payload

    async def text(self) -> str:
        return self._text


class FakeSession:
    closed = False

    async def close(self) -> None:
        self.closed = True

    responses: ClassVar[list[FakeAsyncResponse]] = []

    def __init__(self, **_: object) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)

    def get(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)

    def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        options = {
            key: value
            for key, value in kwargs.items()
            if value is not None and key != "timeout"
        }
        if args[0] == "GET":
            return self.get(*args[1:], **options)
        assert args[0] == "POST"
        return self.post(*args[1:], **options)


class FakeAsyncFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    async def __aenter__(self) -> Self:
        self._file = self._path.open("wb")
        return self

    async def __aexit__(self, *args: object) -> None:
        assert self._file is not None
        self._file.close()

    async def write(self, chunk: bytes) -> int:
        assert self._file is not None
        return self._file.write(chunk)


_T = TypeVar("_T")


class ClientRuntime:
    def __init__(self, runner: asyncio.Runner) -> None:
        self.runner = runner

    def run(self, value: Awaitable[_T]) -> _T:
        async def resolve() -> _T:
            return await value

        return self.runner.run(resolve())


@pytest.fixture
def client_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[ClientRuntime]:

    transports = []
    bridges = []
    init_transport = HTTPTransport.__init__
    init_bridge = TransportBridge.__init__

    def transport_init(  # ruff: ignore[too-many-arguments] -- mirrors transport constructor
        self: HTTPTransport,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        init_transport(self, host, timeout, headers, api_key=api_key, secure=secure)
        transports.append(self)

    def bridge_init(self: TransportBridge, transport: HTTPTransport) -> None:
        init_bridge(self, transport)
        bridges.append(self)

    monkeypatch.setattr(HTTPTransport, "__init__", transport_init)
    monkeypatch.setattr(TransportBridge, "__init__", bridge_init)
    with asyncio.Runner() as runner:
        yield ClientRuntime(runner)
        for bridge in bridges:
            bridge.close()
        for transport in transports:
            if transport._loop is runner.get_loop():  # ruff: ignore[private-member-access]
                runner.run(transport.aclose())
