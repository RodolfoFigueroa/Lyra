"""Installed startup shares live registrations without manifest files or I/O."""

from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, ClassVar
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.config import InstalledPluginConfig
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency
from lyra.sdk.loading import PluginLoadError
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from lyra.server.registry import Catalog
from lyra.server.worker import load_runner_metric_entries
from tests.config_helpers import load_test_config


class MemoryIO(BaseIO):
    events: ClassVar[list[str]] = []

    def setup(self) -> None:
        self.events.append("setup")

    def close(self) -> None:
        self.events.append("close")


class Parameters(MetricParameters):
    value: int = Field(default=7, description="Value to emit")


SCHEMA = pa.DataFrameSchema(
    {
        "value": pa.Column(
            int, checks=pa.Check.ge(0), description="Value", metadata={"unit": None}
        )
    },
    strict=True,
)


@metric(
    code_revision="1",
    name="source",
    description="Source",
    outputs={"data": SCHEMA},
    private=True,
)
def source(
    parameters: Parameters, memory: Annotated[MemoryIO, Resource("memory")]
) -> pd.DataFrame:
    memory.events.append("source")
    return pd.DataFrame({"value": [parameters.value]})


@metric(
    code_revision="1",
    name="root",
    description="Root",
    outputs={"answer": SCHEMA},
    inputs={"data": Dependency("data", producer="source")},
)
def root(data: pd.DataFrame) -> pd.DataFrame:
    MemoryIO.events.append("root")
    return data


def install(monkeypatch: pytest.MonkeyPatch) -> Mock:
    definitions = {
        "source-plugin": PluginDefinition(
            metrics=[source], resources={"memory": MemoryIO}
        ),
        "root-plugin": PluginDefinition(metrics=[root]),
    }

    def distribution(name: str) -> SimpleNamespace:
        definition = definitions[name]
        entry = SimpleNamespace(
            name="plugin",
            group="lyra.plugins",
            value=f"{name}:create_plugin",
            load=lambda: lambda: definition,
        )
        return SimpleNamespace(entry_points=[entry], version="1.0")

    lookup = Mock(side_effect=distribution)
    monkeypatch.setattr(metadata, "distribution", lookup)
    MemoryIO.events.clear()
    return lookup


def test_api_worker_and_local_keep_live_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = install(monkeypatch)
    config = load_test_config(tmp_path)
    config.plugins.installed = [
        InstalledPluginConfig(distribution="source-plugin"),
        InstalledPluginConfig(distribution="root-plugin", routing={"root": "heavy"}),
        InstalledPluginConfig(distribution="disabled-plugin", enabled=False),
    ]
    api = Catalog.load(config.plugins)
    entries = load_runner_metric_entries("heavy", config=config)
    local = LocalRunner(["Source_Plugin", "root-plugin"])
    assert set(entries) == {"root"}
    assert entries["root"].queue == "heavy"
    assert set(entries["root"].implementations) == {"source-plugin", "root-plugin"}
    for catalog in [api.compiled, entries["root"].catalog, local.catalog]:
        assert catalog.graph == api.compiled.graph
        binding = catalog.implementations["source-plugin"]
        assert isinstance(binding.prepare_parameters("source", {}), Parameters)
        assert binding.output_schemas("source")["data"].checks == SCHEMA.checks
        with pytest.raises(pa.errors.SchemaError):
            binding.output_schemas("source")["data"].validate(
                pd.DataFrame({"value": [-1]})
            )
    assert api.discovery.inspect("root").node == "root"
    assert MemoryIO.events == []
    assert {call.args[0] for call in lookup.call_args_list} == {
        "source-plugin",
        "root-plugin",
    }
    assert local.run("root", inputs={}).tables["answer"].value.tolist() == [7]
    assert MemoryIO.events == ["setup", "source", "root", "close"]


@pytest.mark.parametrize("interface", ["api", "worker", "local"])
def test_enabled_import_failure_aborts_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interface: str
) -> None:
    entry = SimpleNamespace(
        name="plugin",
        group="lyra.plugins",
        value="broken:create_plugin",
        load=Mock(side_effect=ImportError("missing import dependency")),
    )
    monkeypatch.setattr(
        metadata, "distribution", lambda _: SimpleNamespace(entry_points=[entry])
    )
    config = load_test_config(tmp_path)
    config.plugins.installed = [InstalledPluginConfig(distribution="broken")]
    start = {
        "api": lambda: Catalog.load(config.plugins),
        "worker": lambda: load_runner_metric_entries("heavy", config=config),
        "local": lambda: LocalRunner(["broken"]),
    }[interface]
    with pytest.raises(PluginLoadError, match="missing import dependency"):
        start()


@pytest.mark.parametrize("change", ["code", "declarations", "routing"])
def test_deployment_revision_is_shared_and_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    install(monkeypatch)
    config = load_test_config(tmp_path)
    config.plugins.installed = [
        InstalledPluginConfig(distribution="source-plugin"),
        InstalledPluginConfig(distribution="root-plugin", routing={"root": "heavy"}),
    ]
    api = Catalog.load(config.plugins, build_revision=config.build_revision)
    for pool in config.workers:
        entries = load_runner_metric_entries(pool, config=config)
        for entry in entries.values():
            assert entry.catalog.build_revision == api.compiled.build_revision
            assert entry.catalog.computation.nodes == api.compiled.computation.nodes
    # Deployment tooling assigns a new immutable token for each kind of change.
    changed = type(config).model_validate(
        {**config.model_dump(), "build_revision": f"build-2-{change}"}
    )
    updated = Catalog.load(changed.plugins, build_revision=changed.build_revision)
    assert updated.compiled.build_revision != api.compiled.build_revision
    assert updated.discovery.fingerprint == api.discovery.fingerprint
    assert updated.compiled.computation.nodes == api.compiled.computation.nodes
    config.workers["heavy"].queues = [config.plugins.default_queue]
    worker = load_runner_metric_entries("heavy", config=config)
    assert worker["source"].catalog.build_revision == api.compiled.build_revision
    assert (
        Catalog.load(config.plugins).discovery.fingerprint == api.discovery.fingerprint
    )
    assert LocalRunner(["source-plugin", "root-plugin"]).catalog.build_revision is None
