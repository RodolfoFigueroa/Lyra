import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from fastapi import Response
from lyra.sdk.models.observability import DatabaseHealth
from redis.exceptions import RedisError

from lyra_app.routes import admin, health
from lyra_app.runtime import ApplicationRuntime
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
)


class FakeRedisAsync:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail

    async def ping(self) -> bool:
        if self.fail:
            raise RedisError
        return self.available


class FakeRedisSync:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail

    def ping(self) -> bool:
        if self.fail:
            raise RedisError
        return self.available


def _configure_admin(
    tmp_path: Path,
    *,
    metric_queues: dict[str, str] | None = None,
    plugins: list[Path] | None = None,
) -> ApplicationRuntime:
    config = load_test_config(tmp_path, metric_queues=metric_queues, plugins=plugins)
    return make_runtime(config)


def test_readiness_reports_healthy_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = cast("Any", SimpleNamespace(async_redis=FakeRedisAsync()))

    async def database_health(  # ruff: ignore[unused-async] -- awaited double
        *_: object,
    ) -> DatabaseHealth:
        return DatabaseHealth(status="ok")

    monkeypatch.setattr(health, "database_health", database_health)
    database = cast(
        "Any",
        SimpleNamespace(config=SimpleNamespace(readiness_timeout_seconds=1.0)),
    )
    http_response = Response()

    runtime.database = database
    runtime.config = SimpleNamespace(
        redis=SimpleNamespace(readiness_timeout_seconds=1.0)
    )
    response = asyncio.run(health.readiness(http_response, runtime))

    assert http_response.status_code == 200
    assert http_response.headers["Cache-Control"] == "no-store"
    assert response.status == "ready"
    assert response.redis.status == "ok"
    assert response.database.status == "ok"


def test_readiness_reports_unavailable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = cast("Any", SimpleNamespace(async_redis=FakeRedisAsync(fail=True)))

    async def database_health(  # ruff: ignore[unused-async] -- awaited double
        *_: object,
    ) -> DatabaseHealth:
        return DatabaseHealth(status="ok")

    monkeypatch.setattr(health, "database_health", database_health)
    database = cast(
        "Any",
        SimpleNamespace(config=SimpleNamespace(readiness_timeout_seconds=1.0)),
    )
    http_response = Response()

    runtime.database = database
    runtime.config = SimpleNamespace(
        redis=SimpleNamespace(readiness_timeout_seconds=1.0)
    )
    response = asyncio.run(health.readiness(http_response, runtime))

    assert http_response.status_code == 503
    assert response.status == "not_ready"
    assert response.redis.status == "unavailable"


def test_stalled_database_check_does_not_block_liveness() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowConnectionContext:
        async def __aenter__(self) -> Self:
            entered.set()
            await release.wait()
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        @staticmethod
        async def execute(*_: object) -> None:
            return None

    database = cast(
        "Any",
        SimpleNamespace(
            require_async_engine=lambda: SimpleNamespace(connect=SlowConnectionContext)
        ),
    )

    async def exercise() -> None:
        database_check = asyncio.create_task(
            health.database_health(database, timeout_seconds=10.0)
        )
        await entered.wait()
        live = await asyncio.wait_for(health.liveness(), timeout=0.1)
        assert live.status == "ok"
        release.set()
        await database_check

    asyncio.run(exercise())


def test_admin_status_excludes_secrets(tmp_path: Path) -> None:
    runtime = _configure_admin(tmp_path)
    runtime.redis = cast("Any", FakeRedisSync())

    response = admin.get_status(runtime)
    payload = response.model_dump_json()

    assert response.redis.status == "ok"
    assert response.metric_count == 0
    assert "admin-secret" not in payload
    assert "postgres-secret" not in payload
    assert "client_email" not in payload


def test_config_summary_excludes_secrets(tmp_path: Path) -> None:
    runtime = _configure_admin(tmp_path)

    response = admin.get_config_summary(runtime)
    payload = response.model_dump_json()

    assert response.default_queue == "interactive"
    assert "admin-secret" not in payload
    assert "postgres-secret" not in payload
    assert "service-account" not in payload
    assert "client_email" not in payload


def test_catalog_metadata_reports_empty_catalog(tmp_path: Path) -> None:
    runtime = _configure_admin(tmp_path)

    response = admin.get_catalog(runtime)

    assert response.metric_count == 0
    assert response.metric_names == []
    assert response.installed_plugins == []
    assert response.metric_queues == {}


def test_catalog_metadata_reports_smoke_directory_plugin(tmp_path: Path) -> None:
    runtime = _configure_admin(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        plugins=[SMOKE_PLUGIN_DIR],
    )

    response = admin.get_catalog(runtime)

    assert response.metric_count == 5
    assert response.metric_names == [
        "example_city_population",
        "example_population_baseline",
        "smoke_progress_metric",
        "smoke_summary_metric",
        "smoke_table_metric",
    ]
    assert response.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert response.installed_plugins[0].version == "0.1.0"
    assert response.metric_queues == SMOKE_METRIC_QUEUES
    assert response.catalog_fingerprint
    assert SMOKE_PLUGIN_DIR.exists()
