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
from lyra.contracts.errors import PluginDefinitionError, ResourceConfigurationError
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency
from lyra.sdk.loading import PluginLoadError
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from lyra.server.registry import Catalog
from lyra.server.worker import initialize_worker, require_worker_runtime
from tests.config_helpers import load_test_config
from tests.provider_helpers import FakeProvider


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
    entries = initialize_worker("heavy", config=config)
    local = LocalRunner(["Source_Plugin", "root-plugin"])
    assert entries.catalog.metric_queues["root"] == "heavy"
    for catalog in [api.compiled, entries.catalog.compiled, local.catalog]:
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
        "worker": lambda: initialize_worker("heavy", config=config),
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
        entries = initialize_worker(pool, config=config)
        assert entries.catalog.compiled.build_revision == api.compiled.build_revision
        assert (
            entries.catalog.compiled.computation.nodes == api.compiled.computation.nodes
        )
    # Deployment tooling assigns a new immutable token for each kind of change.
    changed = type(config).model_validate(
        {**config.model_dump(), "build_revision": f"build-2-{change}"}
    )
    updated = Catalog.load(changed.plugins, build_revision=changed.build_revision)
    assert updated.compiled.build_revision != api.compiled.build_revision
    assert updated.discovery.fingerprint == api.discovery.fingerprint
    assert updated.compiled.computation.nodes == api.compiled.computation.nodes
    config.workers["heavy"].queues = [config.plugins.default_queue]
    worker = initialize_worker("heavy", config=config)
    assert worker.catalog.compiled.build_revision == api.compiled.build_revision
    assert (
        Catalog.load(config.plugins).discovery.fingerprint == api.discovery.fingerprint
    )
    assert LocalRunner(["source-plugin", "root-plugin"]).catalog.build_revision is None


def test_installed_provider_with_cross_plugin_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Places(FakeProvider):
        memory: Annotated[MemoryIO, Resource("memory")]

    definitions = {
        "places": PluginDefinition(resources={"places": Places}),
        "shared": PluginDefinition(resources={"places": Places}),
        "metrics": PluginDefinition(metrics=[source], resources={"memory": MemoryIO}),
    }
    monkeypatch.setattr(
        "lyra.server.plugins.load_installed_plugin_definition", definitions.__getitem__
    )
    monkeypatch.setattr(
        "lyra.runtime.local.load_installed_plugin_definition", definitions.__getitem__
    )
    config = load_test_config(tmp_path)
    config.plugins.installed = [
        InstalledPluginConfig(distribution=name) for name in definitions
    ]
    local = LocalRunner(list(definitions)).catalog
    installed = initialize_worker("heavy", config=config).catalog.compiled
    for catalog in (local, installed):
        assert set(catalog.provider_registry) == {Places.id}
        assert [p.id for p in catalog.manifests["places"].providers] == [Places.id]
        assert (
            catalog.manifests["shared"].providers
            == catalog.manifests["places"].providers
        )
        assert catalog.manifests["metrics"].providers == ()
    Places.code_revision = "next"
    rebuilt = LocalRunner.from_definitions(
        {k: v.definition for k, v in local.implementations.items()}
    ).catalog
    assert rebuilt.provider_registry[Places.id].scope.code_revision == "next"
    assert (
        local.manifests["shared"].providers[0].contract.provider_scope
        != rebuilt.manifests["shared"].providers[0].contract.provider_scope
    )


@pytest.mark.parametrize("key", ["places", "alias"])
def test_worker_rejects_conflicting_registrations_before_replacing_catalog(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definitions = {"first": PluginDefinition(resources={"places": FakeProvider})}
    config = load_test_config(tmp_path)
    config.plugins.installed = [InstalledPluginConfig(distribution="first")]
    monkeypatch.setattr(
        "lyra.server.plugins.load_installed_plugin_definition", definitions.__getitem__
    )
    initialize_worker("heavy", config=config)
    # Two separate classes can have identical serializable resource metadata.
    duplicate = type(
        FakeProvider.__name__, (FakeProvider,), {"__module__": FakeProvider.__module__}
    )
    definitions["second"] = PluginDefinition(
        resources={key: duplicate if key == "places" else FakeProvider}
    )
    config.plugins.installed.append(InstalledPluginConfig(distribution="second"))
    error = ResourceConfigurationError if key == "places" else PluginDefinitionError
    with pytest.raises(error, match="Conflicting"):
        initialize_worker("lightweight", config=config)
    runtime = require_worker_runtime()
    assert runtime.queues == {"heavy"}
    assert set(runtime.catalog.compiled.manifests) == {"first"}
