"""Startup-only configuration and offline operator validation contracts."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from lyra.api import AsyncLyraAdminClient, LyraAdminClient, admin_cli

from lyra_app.main import create_app
from lyra_app.mcp.tools import InProcessLyraBackend
from lyra_app.routes import admin, jobs, metrics
from lyra_app.runtime import require_runtime
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime


def test_offline_validation_needs_no_credentials_or_runtime_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "portable.toml"
    shutil.copyfile(Path(__file__).parents[1] / "config.example.toml", path)
    for name in ("LYRA_ADMIN_API_KEY", "LYRA_AGENT_API_KEY", "LYRA_POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    before = path.read_bytes()
    path.chmod(0o444)

    assert admin_cli.main(["--json", "config", "validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "schema_version": 4}
    assert path.read_bytes() == before
    assert not (tmp_path / "plugins").exists()


@pytest.mark.parametrize(
    ("contents", "expected"),
    [(None, 1), ("[invalid", 2), ("schema_version = 1", 2)],
)
def test_offline_validation_exit_codes(
    tmp_path: Path,
    contents: str | None,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.toml"
    if contents is not None:
        path.write_text(contents)
    assert admin_cli.main(["--json", "config", "validate", str(path)]) == expected
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["kind"] == (
        "operation" if expected == 1 else "usage"
    )


def test_saved_edits_do_not_change_loaded_config_or_registry(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    runtime = make_runtime(config)
    path = tmp_path / "config" / "lyra.toml"
    path.write_text("not valid TOML")
    assert runtime.catalog.get_metrics_info() == []
    assert admin.get_config_summary(runtime).api_port == config.api.port
    assert list(tmp_path.glob("**/plugins.toml")) == []


@pytest.mark.parametrize("operation", ["list", "detail", "submit"])
def test_mcp_requires_active_runtime(operation: str) -> None:
    backend = InProcessLyraBackend(lambda: require_runtime(FastAPI()))
    if operation == "list":
        request = backend.get_metrics()
    elif operation == "detail":
        request = backend.get_metric("smoke_table_metric")
    else:
        request = backend.create_job("smoke_table_metric", {})
    with pytest.raises(RuntimeError, match="runtime is unavailable"):
        asyncio.run(request)


def test_public_catalog_requires_active_runtime() -> None:
    app = FastAPI()
    app.include_router(metrics.router)
    app.include_router(jobs.router)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/metrics", "/metrics/missing"):
                with pytest.raises(RuntimeError, match="runtime is unavailable"):
                    await client.get(path)
            with pytest.raises(RuntimeError, match="runtime is unavailable"):
                await client.post(
                    "/jobs",
                    headers={"Authorization": "Bearer agent-secret"},
                    json={"metric": "missing", "input": {}},
                )

    asyncio.run(exercise())


def test_removed_mutation_routes_and_client_methods() -> None:
    schema_app = FastAPI()
    schema_app.include_router(admin.router)
    operations = {
        (method, path)
        for path, item in schema_app.openapi()["paths"].items()
        for method in item
        if method in {"post", "put", "patch", "delete"}
    }
    assert operations == set()
    for client in (LyraAdminClient("localhost"), AsyncLyraAdminClient("localhost")):
        assert not hasattr(client.plugins, "create")
        assert not hasattr(client.plugins, "sync")
        assert not hasattr(client.routing, "set")
        assert not hasattr(client.catalog, "refresh")
        assert not hasattr(client.workers, "restart")


def test_injected_configuration_is_captured(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    config.api.port = 7654
    runtime = make_runtime(config)
    assert admin.get_config_summary(runtime).api_port == 7654
    assert create_app(config) is not None
