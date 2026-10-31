"""All public transports share ID resolution and service-free inspection."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import LyraClient
from lyra.api.exceptions import DownloadError
from lyra.sdk import LocationAcquisitionError, UnresolvedLocationIDsError
from lyra.sdk.types import JsonObject, validate_json_value
from typing_extensions import override

from lyra_app import main
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME, GetMetricInput, ListMetricsInput
from lyra_app.mcp.tools import InProcessLyraBackend, execute_tool
from lyra_app.registry import Catalog
from tests.config_helpers import api_credentials, load_test_config
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.test_api_client_jobs import FakeAsyncResponse, FakeSession, FakeSyncResponse
from tests.test_location_resolution import SELECTED, Provider, features, selection
from tests.test_mcp_server import (
    _ManagedTestClient,
    _mcp_headers,
    _tool_call_payload,
    _tool_payload,
)


@pytest.mark.usefixtures("dummy_redis_clients")
@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "geometry",
        "unknown",
        "acquisition",
        "missing",
        "duplicate",
        "extra",
        "cleanup",
    ],
)
def test_public_submission_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    request: pytest.FixtureRequest,
) -> None:
    declaration = metric_manifest(name="report", spatial={"blocks", "lots"})
    manifest = plugin_manifest(declaration)
    manifest["location_provider"] = SELECTED.declaration.model_dump(mode="json")
    manifest["resources"] = {"locations": {}}
    (tmp_path / "lyra.plugin.json").write_text(json.dumps(manifest))
    config = load_test_config(tmp_path, plugins=[tmp_path])
    config.database = None
    config.mcp.enabled = True
    monkeypatch.setattr(
        "lyra.sdk.plugin_loader.load_plugin_definition",
        Mock(side_effect=AssertionError("submission loaded an implementation")),
    )
    if scenario == "unknown":
        monkeypatch.setattr(
            Provider,
            "resolve",
            Mock(side_effect=UnresolvedLocationIDsError("test:units", "lot", ["bad"])),
        )
    elif scenario == "acquisition":
        monkeypatch.setattr(
            Provider, "resolve", Mock(side_effect=LocationAcquisitionError("private"))
        )
    elif scenario in {"missing", "duplicate", "extra"}:
        ids = {"missing": ["wrong"], "duplicate": ["a", "a"], "extra": ["a", "extra"]}
        monkeypatch.setattr(
            Provider,
            "resolve",
            Mock(
                return_value=features(list(dict.fromkeys(ids[scenario])))
                .to_geodataframe()
                .iloc[
                    [
                        list(dict.fromkeys(ids[scenario])).index(unit)
                        for unit in ids[scenario]
                    ]
                ]
            ),
        )
    elif scenario == "cleanup":
        monkeypatch.setattr(Provider, "close", Mock(side_effect=OSError("private")))
    payload: dict[str, Any] = {
        "parameters": {"value": 7},
        "blocks": selection("bad" if scenario == "unknown" else "a", "a").model_dump(
            mode="json"
        ),
        "lots": selection("b").model_dump(mode="json"),
    }
    if scenario == "geometry":
        payload["blocks"]["geometry"] = {"type": "Point", "coordinates": [1, 2]}
    app = main.create_app(config, credentials=api_credentials())
    client = _ManagedTestClient(app)
    request.addfinalizer(client.close)
    enqueue = Mock()
    app.state.runtime.jobs.enqueue = enqueue

    def dispatch(body: object) -> httpx.Response:
        return client.post(
            "/jobs",
            json=cast("JsonObject", validate_json_value(body)),
            headers=_mcp_headers(),
        )

    def sync_request(*_args: object, **kwargs: object) -> FakeSyncResponse:
        response = dispatch(kwargs["json"])
        return FakeSyncResponse(
            status_code=response.status_code, payload=response.json()
        )

    class Session(FakeSession):
        @override
        def request(self, *_args: object, **kwargs: object) -> FakeAsyncResponse:
            response = dispatch(kwargs["json"])
            return FakeAsyncResponse(
                status=response.status_code, payload=response.json()
            )

    monkeypatch.setattr("lyra.api.client.sync.requests.request", sync_request)
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", Session)
    sync = LyraClient("example.test", agent_api_key="agent-secret")
    asynchronous = AsyncLyraClient("example.test", agent_api_key="agent-secret")
    submission = {"metric": "report", "input": payload}
    rest = dispatch(submission)
    mcp = _tool_payload(
        client.post(
            "/mcp/",
            json=_tool_call_payload("lyra_run_metric", submission),
            headers=_mcp_headers(),
        )
    )
    _assert_submissions(scenario, (sync, asynchronous), payload, (rest, mcp), enqueue)


def _assert_submissions(
    scenario: str,
    clients: tuple[LyraClient, AsyncLyraClient],
    payload: dict[str, Any],
    responses: tuple[httpx.Response, dict[str, Any]],
    enqueue: Mock,
) -> None:
    sync, asynchronous = clients
    rest, mcp = responses
    if scenario != "geometry":
        sync.raw.create("report", payload)
        asyncio.run(asynchronous.raw.create("report", payload))
        assert rest.status_code == 202
        assert mcp["next_tool"] == "lyra_get_job_result"
        assert enqueue.call_count == 4
        inputs = [call.args[0].input for call in enqueue.call_args_list]
        assert all(value == inputs[0] for value in inputs)
        assert inputs[0] == payload
    else:
        with pytest.raises(DownloadError) as sync_error:
            sync.raw.create("report", payload)
        with pytest.raises(DownloadError) as async_error:
            asyncio.run(asynchronous.raw.create("report", payload))
        assert str(sync_error.value) == str(async_error.value)
        assert "blocks" in str(sync_error.value)
        assert rest.status_code == 422
        assert mcp["error"]["details"] == rest.json()["detail"]
        assert sync_error.value.retryable is False
        enqueue.assert_not_called()


@pytest.mark.usefixtures("dummy_redis_clients")
def test_catalog_inspection_is_service_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    declaration = metric_manifest(name="report", spatial={"blocks", "lots"})
    for name in ("blocks", "lots"):
        declaration["request_schema"]["properties"][name]["description"] = (
            f"Selected {name}."
        )
    manifest = plugin_manifest(declaration)
    manifest["location_provider"] = SELECTED.declaration.model_dump(mode="json")
    manifest["resources"] = {"locations": {}}
    path = tmp_path / "lyra.plugin.json"
    path.write_text(json.dumps(manifest))
    config = load_test_config(tmp_path, plugins=[tmp_path])
    config.database = None
    forbidden = Mock(side_effect=AssertionError("resource access during inspection"))
    monkeypatch.setattr(Provider, "setup", forbidden)
    monkeypatch.setattr(Provider, "resolve", forbidden)
    monkeypatch.setattr("lyra.sdk.resources.SecretReference.resolve", forbidden)
    monkeypatch.setattr("lyra.sdk.plugin_loader.load_plugin_definition", forbidden)
    catalog = Catalog.load(config.plugins)
    backend = InProcessLyraBackend(lambda: Mock(catalog=catalog))
    inventory = asyncio.run(
        execute_tool(
            "lyra_list_metrics",
            ListMetricsInput(),
            backend,
            public_api_base_url="http://test",
        )
    )
    assert (
        inventory.model_dump(mode="json")["location_collections"]
        == catalog.get_metric_catalog().model_dump(mode="json")["location_collections"]
    )
    metric = asyncio.run(
        execute_tool(
            "lyra_get_metric",
            GetMetricInput(metric="report"),
            backend,
            public_api_base_url="http://test",
        )
    )
    for name in ("blocks", "lots"):
        assert (
            metric.model_dump()["request_schema"]["properties"][name]["description"]
            == f"Selected {name}."
        )
    public_catalog = catalog.get_metric_catalog()
    client = _ManagedTestClient(main.create_app(config, credentials=api_credentials()))
    request.addfinalizer(client.close)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json() == public_catalog.model_dump(mode="json")
    assert response.headers["ETag"] == catalog.public_fingerprint
    detail = client.get("/metrics/report")
    assert detail.status_code == 200
    assert detail.json()["request_schema"] == metric.model_dump()["request_schema"]
    schema = client.get("/openapi.json").json()
    assert "/lookups/met-zones" not in schema["paths"]
    assert (
        "location_collections"
        in schema["components"]["schemas"]["MetricCatalogResponse"]["properties"]
    )
    assert set(client.get("/data-types").json()) == {"location_selection_schema"}
    assert "lyra_lookup_met_zone" not in TOOL_CONTRACTS_BY_NAME
    assert not hasattr(LyraClient("example.test"), "lookups")
    assert not hasattr(AsyncLyraClient("example.test"), "lookups")
    monkeypatch.setattr(
        "lyra.api.client.sync.requests.request",
        Mock(
            return_value=FakeSyncResponse(
                payload=public_catalog.model_dump(mode="json")
            )
        ),
    )
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)
    FakeSession.responses = [
        FakeAsyncResponse(payload=public_catalog.model_dump(mode="json"))
    ]
    assert LyraClient("example.test").catalog.metrics() == public_catalog
    assert (
        asyncio.run(AsyncLyraClient("example.test").catalog.metrics()) == public_catalog
    )
    old_fingerprint = catalog.public_fingerprint
    manifest["location_provider"]["collections"][0]["unit_types"].append("station")
    path.write_text(json.dumps(manifest))
    assert Catalog.load(config.plugins).public_fingerprint != old_fingerprint
    forbidden.assert_not_called()
