import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from lyra.server import main
from lyra.server.runtime import require_runtime
from tests.config_helpers import api_credentials, load_test_config

pytestmark = pytest.mark.usefixtures("dummy_redis_clients")


def test_lifespan_owns_mounted_mcp_session_manager(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        calls.append("mcp-start")
        try:
            yield
        finally:
            calls.append("mcp-stop")

    app = main.create_app(load_test_config(tmp_path), credentials=api_credentials())
    app.state.mcp_app = FastAPI(lifespan=mcp_lifespan)

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert calls == ["mcp-start"]

    asyncio.run(run_lifespan())

    assert calls == ["mcp-start", "mcp-stop"]


def test_lifespan_owns_redis_clients(
    tmp_path: Path,
    dummy_redis_clients: tuple[Mock, Mock],
) -> None:
    config = load_test_config(tmp_path)
    app = main.create_app(config, credentials=api_credentials())

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.runtime.redis is dummy_redis_clients[0]
            assert app.state.runtime.async_redis is dummy_redis_clients[1]

    asyncio.run(run_lifespan())

    dummy_redis_clients[0].close.assert_called_once()
    dummy_redis_clients[1].aclose.assert_awaited_once()


def test_run_server_configures_trusted_proxy_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config.api.forwarded_allow_ips = ["127.0.0.1", "172.20.0.0/16"]
    app = FastAPI()
    calls: dict[str, object] = {}

    def create_app(runtime_config: object, **_kwargs: object) -> FastAPI:
        calls["config"] = runtime_config
        return app

    def run(server_app: FastAPI, **kwargs: object) -> None:
        calls["app"] = server_app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(main, "ensure_runtime_directories", Mock())
    monkeypatch.setattr(main, "configure_logging", Mock())
    monkeypatch.setattr(main, "create_app", create_app)
    monkeypatch.setattr(main.uvicorn, "run", run)

    monkeypatch.setattr(main, "load_api_credentials", api_credentials)
    main.run_server(config)

    assert calls == {
        "config": config,
        "app": app,
        "kwargs": {
            "host": config.api.host,
            "port": config.api.port,
            "reload": False,
            "proxy_headers": True,
            "forwarded_allow_ips": ["127.0.0.1", "172.20.0.0/16"],
        },
    }


@pytest.mark.parametrize("failure_stage", ["mcp", "body"])
def test_lifespan_closes_redis_on_failure(
    failure_stage: str,
    tmp_path: Path,
    dummy_redis_clients: tuple[Mock, Mock],
) -> None:
    app = main.create_app(load_test_config(tmp_path), credentials=api_credentials())

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        if failure_stage == "mcp":
            raise RuntimeError(failure_stage)
        yield

    app.state.mcp_app = FastAPI(lifespan=mcp_lifespan)

    async def exercise() -> None:
        message = "body"
        with pytest.raises(RuntimeError, match=failure_stage):
            async with app.router.lifespan_context(app):
                raise RuntimeError(message)

    asyncio.run(exercise())
    dummy_redis_clients[0].close.assert_called_once()
    dummy_redis_clients[1].aclose.assert_awaited_once()


def test_dependency_rejects_inactive_runtime() -> None:
    with pytest.raises(RuntimeError, match="Application runtime is unavailable"):
        require_runtime(FastAPI())
