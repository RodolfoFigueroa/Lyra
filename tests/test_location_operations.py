"""Operation contracts are usable without graph execution or service access."""

from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from pandera.typing import Series
from pandera.typing.geopandas import GeoSeries
from pydantic import ValidationError

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.loading import load_bound_plugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.graph import build_graph
from lyra.runtime.planning.preparation import PreparationError
from lyra.runtime.resources import ResourceScope
from lyra.sdk.context import LocationContext, RunContext
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.server import plugins, registry, worker
from lyra.server.plugins import InstalledPlugin
from test_support.location_helpers import node_contract, producer_result
from test_support.location_operations import (
    OUTPUT,
    MemoryIO,
    Parameters,
    definition,
    select_places,
)
from test_support.location_schema import column, locations


def test_manifest_round_trip_loader_and_direct_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = Mock()
    monkeypatch.setattr(MemoryIO, "setup", setup)
    plugin = definition()
    manifest = plugin.manifest(
        plugin=PluginInfo(name="places", version="1"), factory="places:create"
    )
    assert PluginManifest.model_validate_json(manifest.model_dump_json()) == manifest
    node = manifest.nodes[0]
    assert node.kind == "location_operation"
    assert (
        node.output_contract.family.structure.model_dump()
        == OUTPUT.metadata.model_dump()
    )
    assert set(node.location_inputs) == {"source"}
    assert node.inputs["table"].output == "records"
    assert node.resources == {"memory": "memory"}
    properties = node.request_schema["properties"]
    assert isinstance(properties, dict)
    assert "parameters" in properties
    assert plugin.metric_names == ()
    assert plugin.location_operation_names == ("select_places",)
    assert plugin.describe("select_places").manifest.kind == "location_operation"
    monkeypatch.setattr(
        "lyra.runtime.loading.load_plugin_definition", lambda _: definition()
    )
    binding = load_bound_plugin(manifest)
    assert binding.definition.node_manifests == plugin.node_manifests
    setup.assert_not_called()

    source = locations({"count": "4"})
    context = Mock(spec=LocationContext)
    context.resolve_locations.return_value = source
    table = pd.DataFrame({"a": [1, 2]})
    scope = ResourceScope(plugin.resources)
    with scope.activate():
        memory = scope.get("memory")
        assert isinstance(memory, MemoryIO)
        direct = select_places(source, Parameters(), table, memory, context)
        assert OUTPUT.build_contract(
            direct, node_contract("example:places", "point", OUTPUT)
        ) == OUTPUT.validate(source)
        result = binding.invoke_operation(
            binding.prepare_resolved_invocation(
                "select_places", {"source": source, "parameters": {}}
            ),
            {"table": table},
            lambda _: context,
        )
    assert result.to_features()[0].properties == {"count": 4}
    assert source.to_features()[0].properties == {"count": "4"}
    assert setup.call_count == 1


@pytest.mark.parametrize("context_type", [None, RunContext, LocationContext])
def test_context_contracts(context_type: type | None) -> None:
    source = locations({"count": 1})
    if context_type is None:

        def handler() -> gpd.GeoDataFrame:
            return producer_result(source, OUTPUT)
    else:

        def handler(context: object) -> gpd.GeoDataFrame:
            assert context is supplied
            return producer_result(source, OUTPUT)

        handler.__annotations__["context"] = context_type
    decorated = location_operation(
        output_contract=node_contract("example:places", "point", OUTPUT),
        name="places",
        description="Places",
        output=OUTPUT,
    )(handler)
    assert decorated is handler
    binding = BoundPlugin(PluginDefinition(location_operations=[handler]))
    supplied = Mock(spec=context_type or RunContext)
    assert (
        binding.invoke_operation(
            binding.prepare_resolved_invocation("places", {}), {}, lambda _: supplied
        )
        == source
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        pd.DataFrame(),
        producer_result(locations({"extra": 1}), OUTPUT),
        producer_result(locations({"count": "bad"}), OUTPUT),
    ],
)
def test_bad_results_have_operation_context(value: object) -> None:
    @location_operation(
        output_contract=node_contract("example:places", "point", OUTPUT),
        name="bad",
        description="Bad result",
        output=OUTPUT,
    )
    def handler() -> object:
        return value

    binding = BoundPlugin(PluginDefinition(location_operations=[handler]))
    with pytest.raises((ValueError, TypeError), match="Location operation 'bad'"):
        binding.invoke_operation(
            binding.prepare_resolved_invocation("bad", {}), {}, Mock()
        )


def test_invalid_declarations_and_registration() -> None:
    with pytest.raises(PluginDefinitionError, match=r"Location operation 'missing'"):
        location_operation(
            output_contract=node_contract("example:places", "point", None),
            name="missing",
            description="Missing",
            output=pa.DataFrameSchema({}),
        )(lambda: None)
    for handler in [lambda _unsupported: None, lambda *_args: None]:
        with pytest.raises(PluginDefinitionError, match="Location operation 'bad'"):
            location_operation(
                output_contract=node_contract("example:places", "point", OUTPUT),
                name="bad",
                description="Bad",
                output=OUTPUT,
            )(handler)

    async def asynchronous() -> None:
        await Mock()()

    with pytest.raises(
        PluginDefinitionError, match=r"Location operation 'bad'.*synchronous"
    ):
        location_operation(
            output_contract=node_contract("example:places", "point", OUTPUT),
            name="bad",
            description="Bad",
            output=OUTPUT,
        )(asynchronous)
    with pytest.raises(PluginDefinitionError, match="expected metric"):
        PluginDefinition(metrics=[select_places])
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(location_operations=[select_places, select_places])
    with pytest.raises(PluginDefinitionError, match="Missing or incompatible resource"):
        PluginDefinition(location_operations=[select_places])

    def metric_context(context: LocationContext) -> None:
        context.report_progress(stage="test", current=0)

    with pytest.raises(PluginDefinitionError, match="incompatible annotation"):
        metric(
            name="bad", description="Bad", outputs={"table": pa.DataFrameSchema({})}
        )(metric_context)


def test_operation_only_catalog_and_metric_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @location_operation(
        output_contract=node_contract("example:places", "point", OUTPUT),
        name="places",
        description="Places",
        output=OUTPUT,
    )
    def handler() -> gpd.GeoDataFrame:
        return producer_result(locations({"count": 1}), OUTPUT)

    plugin = PluginDefinition(location_operations=[handler])
    binding = BoundPlugin(plugin)
    manifest = plugin.manifest(
        plugin=PluginInfo(name="places", version="1"), factory="places:create"
    )
    installed = InstalledPlugin("places", manifest, {})
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: [installed])
    catalog = registry.Catalog.load(Mock())
    assert catalog.metric_queues == {}
    assert catalog.discovery.search() == []
    assert catalog.compiled.graph.nodes["places"].node.kind == "location_operation"
    assert build_graph({"places": manifest.nodes}).outputs == {}
    runner = LocalRunner.from_definitions({"places": plugin})
    with pytest.raises(PreparationError, match="Unknown metric family"):
        runner.run("places", {})
    with pytest.raises(PluginDefinitionError, match="not a metric"):
        binding.invoke(binding.prepare_resolved_invocation("places", {}), {}, Mock())
    for updates in [{"location_output": None}, {"kind": "metric"}]:
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(
                manifest.model_dump()
                | {"nodes": [manifest.nodes[0].model_dump() | updates]}
            )


def test_worker_and_installed_routing_exclude_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    @location_operation(
        output_contract=node_contract("example:places", "point", OUTPUT),
        name="places",
        description="Places",
        output=OUTPUT,
    )
    def handler() -> gpd.GeoDataFrame:
        return producer_result(locations(), OUTPUT)

    @metric(
        name="counts", description="Counts", outputs={"counts": pa.DataFrameSchema({})}
    )
    def counts() -> pd.DataFrame:
        return pd.DataFrame()

    plugin = PluginDefinition(metrics=[counts], location_operations=[handler])
    manifest = plugin.manifest(
        plugin=PluginInfo(name="places", version="1"), factory="places:create"
    )
    config = Mock()
    config.installed = [
        Mock(distribution="places", enabled=True, routing={}, manifest_path=None)
    ]
    config.default_queue = "default"
    config.allowed_queues = ["default"]
    monkeypatch.setattr(
        plugins, "load_plugin_manifest", lambda *_args, **_kwargs: manifest
    )
    installed = plugins.load_installed_plugins(config)
    assert installed[0].metric_queues == {"counts": "default"}
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(worker, "load_bound_plugin", lambda _: BoundPlugin(plugin))
    runtime = Mock(plugins=config)
    runtime.get_worker.return_value.queues = ["default"]
    assert set(worker.load_runner_metric_entries("worker", config=runtime)) == {
        "counts"
    }


def test_resolver_context_and_dependency_validation() -> None:
    binding = BoundPlugin(definition())
    invocation = binding.prepare_resolved_invocation(
        "select_places", {"source": locations(), "parameters": {}}
    )
    with pytest.raises(PluginDefinitionError, match="dependency inputs"):
        binding.invoke_operation(invocation, {}, Mock())
    with pytest.raises(
        PluginDefinitionError, match="context requires resolve_locations"
    ):
        binding.invoke_operation(
            invocation, {"table": pd.DataFrame()}, lambda _: Mock(spec=RunContext)
        )


@pytest.mark.parametrize("use_model", [False, True])
def test_plain_pandera_declarations(*, use_model: bool) -> None:

    class Output(pa.DataFrameModel):
        id: Series[str] = pa.Field(description="ID", metadata={"unit": None})
        geometry: GeoSeries = pa.Field(description="Shape", metadata={"unit": None})

    schema = (
        Output
        if use_model
        else pa.DataFrameSchema({"id": column(str), "geometry": column(Geometry)})
    )

    @location_operation(
        output_contract=node_contract("example:places", "point", schema),
        name="places",
        description="Places",
        output=schema,
    )
    def handler() -> gpd.GeoDataFrame:
        return producer_result(locations({}), schema)

    binding = BoundPlugin(PluginDefinition(location_operations=[handler]))
    assert (
        binding.normalize_location_result(
            binding.prepare_resolved_invocation("places", {}),
            producer_result(locations({}), schema),
        ).to_dict()
        == locations({}).to_dict()
    )
