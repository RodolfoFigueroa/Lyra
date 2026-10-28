"""Application isolation and failure cleanup without external resources."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from lyra.sdk import Dependency
from lyra.sdk.models.job import JobCreateRequest

from lyra_app import main
from lyra_app.config import LyraConfig
from lyra_app.mcp.tools import InProcessLyraBackend
from lyra_app.runtime import require_runtime
from tests.config_helpers import api_credentials, load_test_config
from tests.plugin_helpers import plugin_config
from tests.test_plugin_graph import manifest, node


def test_concurrent_applications_own_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = load_test_config(tmp_path / "first")
    second = load_test_config(tmp_path / "second")
    first.mcp.enabled = second.mcp.enabled = False
    second.jobs.result_retention_seconds += 1
    connections = [Mock(), Mock()]
    asynchronous = [Mock(aclose=AsyncMock()), Mock(aclose=AsyncMock())]
    databases = [
        Mock(start=AsyncMock(), close=AsyncMock()),
        Mock(start=AsyncMock(), close=AsyncMock()),
    ]
    sync_factory = Mock(side_effect=connections)
    monkeypatch.setattr(main, "create_sync_client", sync_factory)
    monkeypatch.setattr(main, "create_async_client", Mock(side_effect=asynchronous))
    monkeypatch.setattr(main, "ApplicationDatabaseRuntime", Mock(side_effect=databases))
    monkeypatch.setattr(main, "configure_logging", Mock(side_effect=AssertionError))
    app_a, app_b = (
        main.create_app(
            first,
            credentials=api_credentials().model_copy(
                update={"agent_api_key": "first-key"}
            ),
        ),
        main.create_app(
            second,
            credentials=api_credentials().model_copy(
                update={"agent_api_key": "second-key"}
            ),
        ),
    )
    sync_factory.assert_not_called()
    first.plugins.allowed_queues.append("changed")

    async def exercise() -> None:
        async with app_b.router.lifespan_context(app_b):
            runtime_b = require_runtime(app_b)
            async with app_a.router.lifespan_context(app_a):
                runtime_a = require_runtime(app_a)
                assert runtime_a.credentials.agent_api_key == "first-key"
                assert "changed" not in runtime_a.config.plugins.allowed_queues
                assert runtime_a.catalog is not runtime_b.catalog
                assert runtime_a.jobs.connection is runtime_a.redis
                assert runtime_a.jobs.settings is runtime_a.config.jobs
                assert runtime_a.database is not runtime_b.database
                backend = InProcessLyraBackend(lambda: require_runtime(app_a))
                assert (
                    await backend.get_metrics()
                    == runtime_a.catalog.get_metric_catalog()
                )
                observation = object()
                runtime_a.jobs.observe_job = AsyncMock(return_value=observation)
                assert await backend.observe_job("job") is observation
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app_a), base_url="http://test"
                ) as client:
                    response = await client.get("/metrics")
                    assert response.json() == (await backend.get_metrics()).model_dump(
                        mode="json"
                    )
                    response = await client.post(
                        "/jobs",
                        headers={"Authorization": "Bearer second-key"},
                        json={"metric": "missing", "input": {}},
                    )
                    assert response.status_code == 403
                    response = await client.post(
                        "/jobs",
                        headers={"Authorization": "Bearer first-key"},
                        json={"metric": "missing", "input": {}},
                    )
                    assert response.status_code == 404
            assert app_a.state.runtime is None
            assert require_runtime(app_b) is runtime_b
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_b), base_url="http://test"
            ) as client:
                assert (await client.get("/metrics")).status_code == 200
        assert app_b.state.runtime is None

    asyncio.run(exercise())
    for connection, async_connection, database in zip(
        connections, asynchronous, databases, strict=True
    ):
        connection.close.assert_called_once()
        async_connection.aclose.assert_awaited_once()
        database.close.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", ["catalog", "sync", "async", "database", "mcp", "request", "cleanup"]
)
def test_startup_and_shutdown_release_acquired_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = load_test_config(tmp_path)
    config.mcp.enabled = False
    connection = Mock()
    asynchronous = Mock(aclose=AsyncMock())
    database = Mock(start=AsyncMock(), close=AsyncMock())
    error = RuntimeError("injected failure")
    sync_factory = Mock(return_value=connection)
    async_factory = Mock(return_value=asynchronous)
    monkeypatch.setattr(main, "create_sync_client", sync_factory)
    monkeypatch.setattr(main, "create_async_client", async_factory)
    monkeypatch.setattr(main, "ApplicationDatabaseRuntime", Mock(return_value=database))
    failures = {
        "catalog": (main.Catalog, "load"),
        "sync": (sync_factory, "side_effect"),
        "async": (async_factory, "side_effect"),
        "database": (database.start, "side_effect"),
        "cleanup": (database.close, "side_effect"),
    }
    if failure in failures:
        target, attribute = failures[failure]
        monkeypatch.setattr(
            target,
            attribute,
            Mock(side_effect=error) if failure == "catalog" else error,
        )
    app = main.create_app(config, credentials=api_credentials())

    @asynccontextmanager
    async def failed_mcp(_app: object) -> AsyncIterator[None]:
        raise error
        yield

    if failure == "mcp":
        app.state.mcp_app = Mock(router=Mock(lifespan_context=failed_mcp))

    async def lifespan_failure() -> None:
        async with app.router.lifespan_context(app):
            if failure == "request":
                raise error

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="injected failure"):
            await lifespan_failure()
        assert app.state.runtime is None

    asyncio.run(exercise())
    assert connection.close.call_count == (failure not in {"catalog", "sync"})
    assert asynchronous.aclose.await_count == (
        failure not in {"catalog", "sync", "async"}
    )
    assert database.close.await_count == (failure not in {"catalog", "sync", "async"})
    if failure == "catalog":
        sync_factory.assert_not_called()


def _catalog_config(path: Path, name: str) -> LyraConfig:
    path.mkdir(parents=True, exist_ok=True)
    (path / "lyra.plugin.json").write_text(
        manifest(name, node(name, role="metric")).model_dump_json()
    )
    config = load_test_config(path, plugins=[path])
    config.mcp.enabled = True
    return config


async def _exercise_submission(app: FastAPI, name: str) -> None:
    runtime = require_runtime(app)
    backend = InProcessLyraBackend(lambda: require_runtime(app))
    runtime.jobs.enqueue = Mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        catalog = await client.get("/metrics")
        assert catalog.json() == (await backend.get_metrics()).model_dump(mode="json")
        assert [metric["name"] for metric in catalog.json()["metrics"]] == [name]
        response = await client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {name}-agent"},
            json={"metric": name, "input": {}},
        )
        assert response.status_code == 202
        await backend.create_job(JobCreateRequest(metric=name, input={}))
        assert runtime.jobs.enqueue.call_count == 2
        for call in runtime.jobs.enqueue.call_args_list:
            assert call.args[2] is runtime.catalog.get_metric_entry(name)
        assert (
            await client.get(
                "/admin/catalog", headers={"Authorization": f"Bearer {name}-admin"}
            )
        ).status_code == 200
        assert (
            await client.get(
                "/admin/catalog", headers={"Authorization": "Bearer wrong"}
            )
        ).status_code == 403
        assert (await client.post("/mcp/", json={})).status_code == 401
        assert (
            await client.post(
                "/mcp/", headers={"Authorization": "Bearer wrong"}, json={}
            )
        ).status_code == 403


def test_distinct_catalogs_and_mounted_mcp_share_application_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = [_catalog_config(tmp_path / name, name) for name in ("alpha", "beta")]
    monkeypatch.setattr(main, "create_sync_client", lambda _: Mock())
    monkeypatch.setattr(main, "create_async_client", lambda _: Mock(aclose=AsyncMock()))
    monkeypatch.setattr(
        main,
        "ApplicationDatabaseRuntime",
        lambda _, **_kwargs: Mock(start=AsyncMock(), close=AsyncMock()),
    )
    apps = [
        main.create_app(
            config,
            credentials=api_credentials().model_copy(
                update={
                    "agent_api_key": f"{name}-agent",
                    "admin_api_key": f"{name}-admin",
                }
            ),
        )
        for name, config in zip(("alpha", "beta"), configs, strict=True)
    ]

    async def exercise() -> None:
        async with apps[1].router.lifespan_context(apps[1]):
            async with apps[0].router.lifespan_context(apps[0]):
                await _exercise_submission(apps[0], "alpha")
                await _exercise_submission(apps[1], "beta")
                assert (
                    require_runtime(apps[0]).redis is not require_runtime(apps[1]).redis
                )
                assert (
                    require_runtime(apps[0]).async_redis
                    is not require_runtime(apps[1]).async_redis
                )
            await _exercise_submission(apps[1], "beta")

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", ["manifest", "duplicate", "graph"])
def test_invalid_catalog_aborts_before_acquiring_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    config = _catalog_config(tmp_path / "plugin", "public")
    source = config.plugins.installed[0].manifest_path
    assert source is not None
    if invalid == "manifest":
        source.write_text('{"invalid": true}')
    elif invalid == "duplicate":
        other = tmp_path / "other"
        other.mkdir()
        (other / "lyra.plugin.json").write_text(
            manifest("other", node("public", role="metric")).model_dump_json()
        )
        config.plugins.installed.append(plugin_config(other))
    else:
        source.write_text(
            manifest(
                "public",
                node("public", role="metric", inputs={"data": Dependency("missing")}),
            ).model_dump_json()
        )
    acquire = Mock(
        side_effect=AssertionError("Service acquisition before catalog validation")
    )
    monkeypatch.setattr(main, "create_sync_client", acquire)
    monkeypatch.setattr(main, "create_async_client", acquire)
    monkeypatch.setattr(main, "ApplicationDatabaseRuntime", acquire)
    app = main.create_app(config, credentials=api_credentials())

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            pytest.fail("Invalid catalogs cannot serve requests")

    with pytest.raises(
        RuntimeError if invalid == "manifest" else ValueError, match=r".+"
    ) as error:
        asyncio.run(exercise())
    if invalid == "manifest":
        assert error.value.__cause__ is not None
    acquire.assert_not_called()
    assert app.state.runtime is None
