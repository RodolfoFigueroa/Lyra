"""Reusable worker graph integration fixtures."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandera.pandas as pa
import pytest

from lyra.contracts.plugins import PluginInfo
from lyra.runtime import loading
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import BaseIO
from lyra.server import registry, worker
from lyra.server.plugins import InstalledPlugin
from lyra.server.runtime import ApplicationRuntime
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime

TABLE = pa.DataFrameSchema({})


VALUES = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
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
    artifact_dir: Path | None = None,
) -> tuple[list[InstalledPlugin], ApplicationRuntime]:
    plugins = [
        InstalledPlugin(
            name,
            definition.manifest(
                plugin=PluginInfo(name=name, version="1"), factory=f"{name}:create"
            ),
            {
                n: "heavy" if n == "root" else "lightweight"
                for n in definition.metric_names
            },
        )
        for name, definition in definitions.items()
    ]
    config = load_test_config(tmp_path)
    config.artifact_dir = artifact_dir or tmp_path / "artifacts"
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(
        loading,
        "load_plugin_definition",
        lambda ref: definitions[ref.split(":")[0]],
    )
    runtime = make_runtime(config)
    worker.refresh_runner_registry("heavy", config=config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    return plugins, runtime


def native_job(monkeypatch: pytest.MonkeyPatch, root: str = "root") -> SimpleNamespace:
    entry = worker.RUNNER_REGISTRY[root]
    assert entry.contract.kind == "metric"
    native = SimpleNamespace(
        id="graph-job",
        origin=entry.queue,
        worker_name="fake-worker",
        meta={},
        save_meta=Mock(),
    )
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    return native
