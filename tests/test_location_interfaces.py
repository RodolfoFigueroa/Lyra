"""All public transports share ID resolution and service-free inspection."""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, create_autospec

import httpx
import pytest
from typing_extensions import override

from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import LyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import LocationAcquisitionError, UnresolvedLocationIDsError
from lyra.contracts.types import JsonObject, validate_json_value
from lyra.server import main
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.registry import Catalog
from test_support.location_resolution import (
    PROVIDERS,
    SELECTED,
    Provider,
    features,
    selection,
)
from test_support.mcp import call_tool
from tests.client_helpers import (
    ClientRuntime,
    FakeAsyncResponse,
    FakeSession,
    FakeSyncResponse,
    install_sync_handler,
)
from tests.config_helpers import api_credentials, load_test_config
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.mcp_helpers import (
    ManagedTestClient,
    mcp_headers,
    tool_call_payload,
    tool_payload,
)
from tests.queue_helpers import submitted_input


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
    client_runtime: ClientRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    request: pytest.FixtureRequest,
) -> None:
    declaration = metric_manifest(name="report", spatial={"blocks", "lots"})
    for value in declaration["location_inputs"].values():
        value["contract"] = PROVIDERS[Provider.id].contract.model_dump(mode="json")
    manifest = plugin_manifest(declaration)
    manifest["providers"] = [SELECTED.declaration.model_dump(mode="json")]
    manifest["resources"] = {"locations": {}}
    (tmp_path / "lyra.plugin.json").write_text(json.dumps(manifest))
    config = load_test_config(tmp_path, plugins=[tmp_path])
    config.mcp.enabled = True
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
                .copy(deep=True)
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
    client = ManagedTestClient(app)
    request.addfinalizer(client.close)
    enqueue = Mock()
    app.state.runtime.jobs.enqueue = enqueue

    def dispatch(body: object) -> httpx.Response:
        return client.post(
            "/jobs",
            json=cast("JsonObject", validate_json_value(body)),
            headers=mcp_headers(),
        )

    class Session(FakeSession):
        @override
        def dispatch(self, *_args: object, **kwargs: object) -> FakeAsyncResponse:
            response = dispatch(kwargs["json"])
            return FakeAsyncResponse(
                status=response.status_code, payload=response.json()
            )

    monkeypatch.setattr("httpx.AsyncClient", Session)

    def sync_request(_method: str, _url: str, **kwargs: object) -> FakeSyncResponse:
        response = dispatch(kwargs["json"])
        return FakeSyncResponse(
            status_code=response.status_code, payload=response.json()
        )

    install_sync_handler(monkeypatch, sync_request)
    sync = LyraClient("example.test", agent_api_key="agent-secret")
    asynchronous = AsyncLyraClient("example.test", agent_api_key="agent-secret")
    submission = {
        "metric": "report",
        "inputs": {k: v for k, v in payload.items() if k != "parameters"},
        "parameters": payload.get("parameters", {}),
    }
    rest = dispatch(submission)
    mcp = tool_payload(
        client.post(
            "/mcp/",
            json=tool_call_payload("lyra_run_metric", submission),
            headers=mcp_headers(),
        )
    )
    _assert_submissions(
        scenario, (sync, asynchronous, client_runtime), payload, (rest, mcp), enqueue
    )


def _assert_submissions(
    scenario: str,
    clients: tuple[LyraClient, AsyncLyraClient, ClientRuntime],
    payload: dict[str, Any],
    responses: tuple[httpx.Response, dict[str, Any]],
    enqueue: Mock,
) -> None:
    sync, asynchronous, client_runtime = clients
    rest, mcp = responses
    if scenario != "geometry":
        sync.jobs.create(
            "report",
            inputs={k: v for k, v in payload.items() if k != "parameters"},
            **(
                {"parameters": payload["parameters"]} if "parameters" in payload else {}
            ),
        )
        client_runtime.run(
            asynchronous.jobs.create(
                "report",
                inputs={k: v for k, v in payload.items() if k != "parameters"},
                **(
                    {"parameters": payload["parameters"]}
                    if "parameters" in payload
                    else {}
                ),
            )
        )
        assert rest.status_code == 202
        assert mcp["next_tool"] == "lyra_get_job_result"
        assert enqueue.call_count == 4
        inputs = [submitted_input(call.args[0]) for call in enqueue.call_args_list]
        assert all(value == inputs[0] for value in inputs)
        assert inputs[0] == payload
    else:
        with pytest.raises(DownloadError) as sync_error:
            sync.jobs.create(
                "report",
                inputs={k: v for k, v in payload.items() if k != "parameters"},
                **(
                    {"parameters": payload["parameters"]}
                    if "parameters" in payload
                    else {}
                ),
            )
        with pytest.raises(DownloadError) as async_error:
            client_runtime.run(
                asynchronous.jobs.create(
                    "report",
                    inputs={k: v for k, v in payload.items() if k != "parameters"},
                    **(
                        {"parameters": payload["parameters"]}
                        if "parameters" in payload
                        else {}
                    ),
                )
            )
        assert str(sync_error.value) == str(async_error.value)
        assert "blocks" in str(sync_error.value)
        assert rest.status_code == 422
        assert mcp["error"]["details"] == rest.json()["detail"]
        assert sync_error.value.retryable is False
        enqueue.assert_not_called()


@pytest.mark.usefixtures("dummy_redis_clients")
def test_catalog_inspection_is_service_free(
    client_runtime: ClientRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    declaration = metric_manifest(name="report", spatial={"blocks", "lots"})
    for name in ("blocks", "lots"):
        declaration["location_inputs"][name]["description"] = f"Selected {name}."
    for value in declaration["location_inputs"].values():
        value["contract"] = PROVIDERS[Provider.id].contract.model_dump(mode="json")
    manifest = plugin_manifest(declaration)
    manifest["providers"] = [SELECTED.declaration.model_dump(mode="json")]
    manifest["resources"] = {"locations": {}}
    path = tmp_path / "lyra.plugin.json"
    path.write_text(json.dumps(manifest))
    config = load_test_config(tmp_path, plugins=[tmp_path])
    forbidden = Mock(side_effect=AssertionError("resource access during inspection"))
    monkeypatch.setattr(Provider, "setup", forbidden)
    monkeypatch.setattr(
        Provider, "resolve", create_autospec(Provider.resolve, side_effect=forbidden)
    )
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    catalog = Catalog.load(config.plugins)
    backend = InProcessLyraBackend(lambda: Mock(catalog=catalog))
    inventory = client_runtime.run(call_tool(backend, "lyra_list_metrics", {}))
    assert not inventory.get("isError", False)
    assert inventory["structuredContent"]["metrics"] == [
        item.model_dump(mode="json") for item in catalog.discovery.search()
    ]
    metric = client_runtime.run(
        call_tool(backend, "lyra_get_metric", {"metric": "report"})
    )
    assert not metric.get("isError", False)
    assert metric["structuredContent"] == catalog.discovery.inspect(
        "report"
    ).model_dump(mode="json")
    public_catalog = catalog.discovery.search()
    client = ManagedTestClient(main.create_app(config, credentials=api_credentials()))
    request.addfinalizer(client.close)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json() == [item.model_dump(mode="json") for item in public_catalog]
    assert response.headers["ETag"] == catalog.discovery.fingerprint
    detail = client.get("/metrics/report")
    assert detail.status_code == 200
    assert detail.json()["accepted_selections"]
    schema = client.get("/openapi.json").json()
    assert "MetricInspection" in schema["components"]["schemas"]
    assert set(client.get("/data-types").json()) == {"location_selection_schema"}
    monkeypatch.setattr("httpx.AsyncClient", FakeSession)
    FakeSession.responses = [
        FakeAsyncResponse(
            payload=[item.model_dump(mode="json") for item in public_catalog]
        )
    ]
    install_sync_handler(
        monkeypatch,
        lambda *_args, **_kwargs: FakeSyncResponse(
            payload=[item.model_dump(mode="json") for item in public_catalog]
        ),
    )
    assert LyraClient("example.test").catalog.metrics() == public_catalog
    assert (
        client_runtime.run(AsyncLyraClient("example.test").catalog.metrics())
        == public_catalog
    )
    manifest["providers"][0]["resource"] = "alternate_binding"
    manifest["resources"] = {"alternate_binding": {}}
    path.write_text(json.dumps(manifest))
    updated = Catalog.load(config.plugins)
    assert (
        updated.discovery.inspect("report").providers[Provider.id].resource
        == "alternate_binding"
    )
    assert (
        updated.discovery.inspect("report")
        .providers[Provider.id]
        .contract.provider_scope
        == catalog.discovery.inspect("report")
        .providers[Provider.id]
        .contract.provider_scope
    )
    # The discovery ETag tracks visible metadata, including resource bindings.
    assert updated.discovery.fingerprint != catalog.discovery.fingerprint
    assert updated.operational_fingerprint != catalog.operational_fingerprint
    forbidden.assert_not_called()


pytestmark = pytest.mark.usefixtures("client_runtime")
