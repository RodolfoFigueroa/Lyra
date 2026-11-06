"""FastAPI construction and application resource ownership."""

from contextlib import AsyncExitStack, asynccontextmanager
from types import AsyncGeneratorType

from lyra.server._bootstrap import command_imports

with command_imports(__name__):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from redis.exceptions import RedisError

    from lyra.contracts.config import LyraConfig
    from lyra.runtime.artifacts import ArtifactStore
    from lyra.server.config import (
        ApiCredentials,
        ensure_runtime_directories,
        load_api_credentials,
        load_config,
    )
    from lyra.server.db.redis import create_async_client, create_sync_client
    from lyra.server.job_store import JobStore
    from lyra.server.logging_config import configure_logging
    from lyra.server.mcp import create_mcp_app
    from lyra.server.mcp.tools import InProcessLyraBackend
    from lyra.server.registry import Catalog
    from lyra.server.results import ResultService
    from lyra.server.routes import admin, data_types, health, jobs, metrics
    from lyra.server.runtime import ApplicationRuntime, require_runtime
    from lyra.version import APP_VERSION


def create_app(config: LyraConfig, *, credentials: ApiCredentials) -> FastAPI:
    """Construct an application using a deep configuration snapshot.

    Returns:
        The routed application; resources start during its lifespan.
    """
    config = config.model_copy(deep=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGeneratorType:
        try:
            catalog = Catalog.load(config.plugins)
            async with AsyncExitStack() as stack:
                connection = create_sync_client(config.redis)
                stack.callback(connection.close)
                async_connection = create_async_client(config.redis)
                stack.push_async_callback(async_connection.aclose)
                jobs = JobStore(connection, config.jobs)
                app.state.runtime = ApplicationRuntime(
                    config,
                    catalog,
                    connection,
                    async_connection,
                    jobs,
                    credentials,
                    ResultService(jobs, ArtifactStore(config.artifact_dir)),
                )
                mcp_app = getattr(app.state, "mcp_app", None)
                if mcp_app is not None:
                    await stack.enter_async_context(
                        mcp_app.router.lifespan_context(mcp_app)
                    )
                yield
        finally:
            app.state.runtime = None

    app = FastAPI(title="Lyra API", version=APP_VERSION, lifespan=lifespan)
    app.add_exception_handler(RedisError, redis_unavailable_response)
    for router in (
        admin.router,
        health.router,
        jobs.router,
        data_types.router,
        metrics.router,
    ):
        app.include_router(router)
    if config.mcp.enabled:
        mcp_app = create_mcp_app(
            agent_api_key=credentials.agent_api_key,
            public_api_base_url=config.api.public_base_url,
            backend=InProcessLyraBackend(lambda: require_runtime(app)),
        )
        app.state.mcp_app = mcp_app
        app.mount(config.mcp.mount_path, mcp_app)
    return app


def redis_unavailable_response(_request: Request, _exc: Exception) -> JSONResponse:
    """Return explicit unavailability when a native Redis operation fails."""
    return JSONResponse(status_code=503, content={"detail": "Redis is unavailable."})


def run_server(config: LyraConfig | None = None) -> None:
    """Create and run the production Uvicorn server from runtime configuration."""
    runtime_config = load_config() if config is None else config
    ensure_runtime_directories(runtime_config)
    configure_logging(runtime_config)
    credentials = load_api_credentials()
    app = create_app(runtime_config, credentials=credentials)

    uvicorn.run(
        app,
        host=runtime_config.api.host,
        port=runtime_config.api.port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips=runtime_config.api.forwarded_allow_ips,
    )


if __name__ == "__main__":
    run_server()
