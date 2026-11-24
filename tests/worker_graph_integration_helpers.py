"""Reusable worker graph integration fixtures."""

from collections.abc import Callable
from itertools import starmap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandera.pandas as pa
import pytest

from lyra.contracts.references import new_job_id
from lyra.sdk import loading
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import BaseIO
from lyra.server import worker
from lyra.server.plugins import InstalledPlugin
from lyra.server.runtime import ApplicationRuntime
from test_support.table_fixtures import value_schema
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime

TABLE = value_schema()


VALUES = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}, strict=True
)


def install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handlers: list[Callable[..., object]],
    resources: dict[str, type[BaseIO]] | None = None,
) -> tuple[list[InstalledPlugin], ApplicationRuntime]:
    definitions = {
        f"plugin_{i}": PluginDefinition(metrics=[handler], resources=resources)
        for i, handler in enumerate(handlers)
    }
    return install_definitions(tmp_path, monkeypatch, definitions)


def install_definitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    definitions: dict[str, PluginDefinition],
    *,
    result_store_path: Path | None = None,
) -> tuple[list[InstalledPlugin], ApplicationRuntime]:
    plugins = list(starmap(InstalledPlugin, definitions.items()))
    config = load_test_config(tmp_path)
    config.result_store_path = (
        result_store_path or tmp_path / "store" / "results.sqlite3"
    )
    monkeypatch.setattr(
        "lyra.server.plugins.discover_installed_plugins", lambda _: plugins
    )
    monkeypatch.setattr(
        loading,
        "load_plugin_definition",
        lambda ref: definitions[ref.split(":")[0]],
    )
    runtime = make_runtime(config)
    worker.initialize_worker(config=config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    return plugins, runtime


def native_job(
    monkeypatch: pytest.MonkeyPatch, *, job_id: str | None = None
) -> SimpleNamespace:
    native = SimpleNamespace(
        id=job_id or new_job_id(),
        origin=worker.require_worker_config().jobs.queue,
        worker_name="fake-worker",
        meta={},
        save_meta=Mock(),
    )
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    return native
