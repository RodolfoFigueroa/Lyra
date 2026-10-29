import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from lyra_app import main
from lyra_app.config import EarthEngineConfig
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.db.dependencies import require_database_runtime
from tests.config_helpers import api_credentials, database_config, load_test_config

pytestmark = pytest.mark.usefixtures("dummy_database_engines", "dummy_redis_clients")


def test_lifespan_owns_mounted_mcp_session_manager(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncIterator[None]:
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


@pytest.mark.parametrize(
    "earth_engine",
    [
        None,
        EarthEngineConfig(
            project="project", service_account_file=Path("/absent/credential.json")
        ),
    ],
)
def test_lifespan_owns_database_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    earth_engine: EarthEngineConfig | None,
) -> None:
    calls: list[str] = []

    class FakeDatabaseRuntime:
        @staticmethod
        async def start() -> None:
            calls.append("database-start")

        @staticmethod
        async def close() -> None:
            calls.append("database-close")

    monkeypatch.setattr(
        main, "ApplicationDatabaseRuntime", Mock(return_value=FakeDatabaseRuntime())
    )
    config = load_test_config(tmp_path)
    config.earth_engine = earth_engine
    app = main.create_app(config, credentials=api_credentials())

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert calls == ["database-start"]

    asyncio.run(run_lifespan())

    assert calls == [
        "database-start",
        "database-close",
    ]


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

    monkeypatch.setattr(main, "load_api_credentials", lambda _: api_credentials())
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


@pytest.mark.parametrize("failure_stage", ["database", "mcp", "body"])
def test_lifespan_closes_database_on_failure(
    failure_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ApplicationDatabaseRuntime(
        database_config(tmp_path),
        password=api_credentials().require_postgres_password(),
    )
    monkeypatch.setattr(main, "ApplicationDatabaseRuntime", Mock(return_value=runtime))
    app = main.create_app(load_test_config(tmp_path), credentials=api_credentials())
    start = runtime.start
    close = AsyncMock(wraps=runtime.close)
    monkeypatch.setattr(runtime, "close", close)

    async def start_database() -> None:
        await start()
        if failure_stage == "database":
            raise RuntimeError(failure_stage)

    @asynccontextmanager
    async def mcp_lifespan(_: FastAPI) -> AsyncIterator[None]:
        if failure_stage == "mcp":
            raise RuntimeError(failure_stage)
        yield

    monkeypatch.setattr(runtime, "start", start_database)
    app.state.mcp_app = FastAPI(lifespan=mcp_lifespan)

    async def exercise() -> None:
        message = "body"
        with pytest.raises(RuntimeError, match=failure_stage):
            async with app.router.lifespan_context(app):
                raise RuntimeError(message)

    asyncio.run(exercise())
    close.assert_awaited_once()
    assert runtime.async_engine is None


def test_database_dependency_rejects_inactive_runtime() -> None:

    with pytest.raises(RuntimeError, match="Application runtime is unavailable"):
        require_database_runtime(FastAPI())
