import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import Response
from redis.exceptions import RedisError

from lyra.server.routes import admin, health
from lyra.server.runtime import ApplicationRuntime
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


def test_readiness_reports_healthy_dependencies() -> None:
    runtime = cast("Any", SimpleNamespace(async_redis=FakeRedisAsync()))

    http_response = Response()

    runtime.config = SimpleNamespace(
        redis=SimpleNamespace(readiness_timeout_seconds=1.0)
    )
    response = asyncio.run(health.readiness(http_response, runtime))

    assert http_response.status_code == 200
    assert http_response.headers["Cache-Control"] == "no-store"
    assert response.status == "ready"
    assert response.redis.status == "ok"
    assert set(response.model_dump()) == {"status", "api_version", "redis"}


def test_readiness_reports_unavailable_redis() -> None:
    runtime = cast("Any", SimpleNamespace(async_redis=FakeRedisAsync(fail=True)))

    http_response = Response()

    runtime.config = SimpleNamespace(
        redis=SimpleNamespace(readiness_timeout_seconds=1.0)
    )
    response = asyncio.run(health.readiness(http_response, runtime))

    assert http_response.status_code == 503
    assert response.status == "not_ready"
    assert response.redis.status == "unavailable"


def test_stalled_redis_check_does_not_block_liveness() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowRedis:
        @staticmethod
        async def ping() -> bool:
            entered.set()
            await release.wait()
            return True

    async def exercise() -> None:
        redis_check = asyncio.create_task(
            health.redis_health(cast("Any", SlowRedis()), timeout_seconds=10.0)
        )
        await entered.wait()
        live = await asyncio.wait_for(health.liveness(), timeout=0.1)
        assert live.status == "ok"
        release.set()
        await redis_check

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

    assert response.metric_count == 7
    assert response.metric_names == [
        "example_city_population",
        "example_city_source",
        "example_population_baseline",
        "example_selected_source",
        "smoke_progress_metric",
        "smoke_summary_metric",
        "smoke_table_metric",
    ]
    assert response.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert response.installed_plugins[0].version == "0.1.0"
    assert response.metric_queues == {
        **SMOKE_METRIC_QUEUES,
        "example_city_source": "interactive",
        "example_selected_source": "interactive",
    }
    assert response.catalog_fingerprint
    assert SMOKE_PLUGIN_DIR.exists()
