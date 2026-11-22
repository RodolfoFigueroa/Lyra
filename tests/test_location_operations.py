"""Pure operation contracts execute with native provider frames."""

from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import ValidationError

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.locations import LocationSelection
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import resolve_locations
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.server import plugins, registry, worker
from lyra.server.plugins import InstalledPlugin
from test_support.location_operations import (
    Parameters,
    Places,
    definition,
    select_places,
)
from test_support.runtime_inputs import native_inputs


def source_records() -> NativeLocationInput:
    plugin = definition()
    return resolve_locations(
        {"source": LocationSelection(provider=Places.id, ids=["one"])},
        providers=compile_provider_registry({"places": plugin}),
        resources=plugin.resources,
    )["source"]


def test_manifest_round_trip_loader_and_direct_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = Mock()
    monkeypatch.setattr(Places, "setup", setup)
    plugin = definition()
    manifest = plugin.manifest(
        plugin=PluginInfo(name="places", version="1"), factory="places:create"
    )
    node = manifest.nodes[0]
    assert node.kind == "location_operation"
    assert node.output_contract.provider_scope is not None
    assert node.output_contract.provider_scope.provider_id == Places.id
    assert set(node.location_inputs) == {"source"}
    assert node.inputs == {}
    assert node.resources == {}
    properties = node.request_schema["properties"]
    assert isinstance(properties, dict)
    assert "parameters" in properties
    assert plugin.metric_names == ()
    assert plugin.location_operation_names == ("select_places",)
    assert plugin.describe("select_places").manifest.kind == "location_operation"
    binding = BoundPlugin(plugin)
    assert binding.definition.node_manifests == plugin.node_manifests
    setup.assert_not_called()
    assert PluginManifest.model_validate_json(manifest.model_dump_json()) == manifest

    source = source_records()
    direct = select_places(source.frame, parameters=Parameters())
    assert direct == {"one": ("one",)}
    assert setup.call_count == 1


def test_invalid_declarations_and_registration() -> None:
    for handler in [lambda _unsupported: None, lambda *_args: None]:
        with pytest.raises(PluginDefinitionError, match="bad"):
            location_operation(
                inputs={"source": LocationDependency(provider=Places.id)},
                target_provider=Places.id,
                code_revision="1",
                name="bad",
                description="Bad",
            )(handler)

    async def asynchronous(source: gpd.GeoDataFrame) -> None:
        del source
        await Mock()()

    with pytest.raises(PluginDefinitionError, match=r"bad.*synchronous"):
        location_operation(
            inputs={"source": LocationDependency(provider=Places.id)},
            target_provider=Places.id,
            code_revision="1",
            name="bad",
            description="Bad",
        )(asynchronous)
    with pytest.raises(PluginDefinitionError, match="expected metric"):
        PluginDefinition(metrics=[select_places])
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(location_operations=[select_places, select_places])
    with pytest.raises(ValueError, match="provider"):
        compile_live_catalog(
            {"operations": PluginDefinition(location_operations=[select_places])}
        )


def test_operation_only_catalog_and_metric_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = definition()
    binding = BoundPlugin(plugin)
    installed = InstalledPlugin("places", plugin, {})
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: [installed])
    catalog = registry.Catalog.load(Mock())
    assert catalog.metric_queues == {}
    assert catalog.discovery.search() == []
    assert (
        catalog.compiled.graph.nodes["select_places"].node.kind == "location_operation"
    )
    assert catalog.compiled.graph.outputs == {}
    runner = LocalRunner.from_definitions({"places": plugin})
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run("select_places", inputs={})
    with pytest.raises(PluginDefinitionError, match="not a metric"):
        binding.invoke(
            binding.prepare_resolved_invocation(
                "select_places",
                native_inputs({"source": source_records(), "parameters": {}}),
            ),
            {},
            Mock(),
        )
    manifest = catalog.compiled.manifests["places"]
    for updates in [{"location_output": None}, {"kind": "metric"}]:
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(
                manifest.model_dump()
                | {"nodes": [manifest.nodes[0].model_dump() | updates]}
            )


def test_worker_and_installed_routing_exclude_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @metric(
        code_revision="1",
        name="counts",
        description="Counts",
        outputs={"counts": pa.DataFrameSchema({}, strict=True)},
    )
    def counts() -> pd.DataFrame:
        return pd.DataFrame()

    plugin = PluginDefinition(
        metrics=[counts],
        location_operations=[select_places],
        resources={"places": Places},
    )
    config = Mock()
    config.installed = [
        Mock(distribution="places", enabled=True, routing={}, manifest_path=None)
    ]
    config.default_queue = "default"
    config.allowed_queues = ["default"]
    monkeypatch.setattr(
        plugins, "load_installed_plugin_definition", lambda *_args, **_kwargs: plugin
    )
    installed = plugins.load_installed_plugins(config)
    assert installed[0].metric_queues == {"counts": "default"}
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    runtime = Mock(plugins=config)
    runtime.get_worker.return_value.queues = ["default"]
    assert set(worker.load_runner_metric_entries("worker", config=runtime)) == {
        "counts"
    }
