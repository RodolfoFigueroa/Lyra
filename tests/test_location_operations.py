"""Operation contracts are usable without graph execution or service access."""

from typing import Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    BaseIO,
    Dependency,
    Input,
    LocalRunner,
    LocationContext,
    Locations,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    Resource,
    RunContext,
    location_operation,
    metric,
)
from lyra.sdk.graph import build_graph
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from lyra.sdk.plugin_loader import load_validated_plugin_definition
from lyra.sdk.resources import ResourceScope
from pandera.engines.geopandas_engine import Geometry
from pandera.typing import Series
from pandera.typing.geopandas import GeoSeries
from pydantic import Field, ValidationError

from lyra_app import plugins, registry, worker
from lyra_app.plugins import InstalledPlugin
from tests.location_helpers import producer_result
from tests.test_location_schema import column, contract, declaration, locations


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Record count")


class MemoryIO(BaseIO):
    def setup(self) -> None:
        pass

    def close(self) -> None:
        pass


OUTPUT = contract(declaration(count=column(int, coerce=True)))


@location_operation(
    name="select_places",
    description="Select places using supplied records.",
    output=OUTPUT,
    inputs={
        "table": Dependency("records", parameters={"count": Input("parameters.count")})
    },
    scope_from=Input("source"),
)
def select_places(
    source: Locations,
    parameters: Parameters,
    table: pd.DataFrame,
    memory: Annotated[MemoryIO, Resource("memory")],
    context: LocationContext,
) -> gpd.GeoDataFrame:
    assert isinstance(memory, MemoryIO)
    assert len(table) == parameters.count
    context.report_progress(stage="select", current=len(source.ids))
    return producer_result(
        context.resolve_locations(
            source.collection, source.unit_type, list(source.ids)
        ),
        OUTPUT,
    )


def definition() -> PluginDefinition:
    return PluginDefinition(
        location_operations=[select_places], resources={"memory": MemoryIO}
    )


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
    assert node.outputs == {}
    assert node.location_output is not None
    assert (
        node.location_output.model_dump(exclude={"scope"})
        == OUTPUT.metadata.model_dump()
    )
    assert node.spatial_inputs == {"source": "location"}
    assert node.inputs["table"].output == "records"
    assert node.resources == {"memory": "memory"}
    properties = node.request_schema["properties"]
    assert isinstance(properties, dict)
    assert "parameters" in properties
    assert plugin.metric_names == ()
    assert plugin.location_operation_names == ("select_places",)
    assert plugin.describe("select_places").kind == "location_operation"
    monkeypatch.setattr(
        "lyra.sdk.plugin_loader.load_plugin_definition", lambda _: definition()
    )
    assert (
        load_validated_plugin_definition(manifest).node_manifests
        == plugin.node_manifests
    )
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
        assert OUTPUT.build(
            direct, collection="example:places", unit_type="point"
        ) == OUTPUT.validate(source)
        result = plugin.invoke_operation(
            plugin.prepare_resolved_invocation(
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

        def handler(context: object) -> Locations:
            assert context is supplied
            return producer_result(source, OUTPUT)

        handler.__annotations__["context"] = context_type
    decorated = location_operation(
        name="places",
        description="Places",
        output=OUTPUT,
        collection="example:places",
        unit_type="point",
    )(handler)
    assert decorated is handler
    plugin = PluginDefinition(location_operations=[handler])
    supplied = Mock(spec=context_type or RunContext)
    assert (
        plugin.invoke_operation(
            plugin.prepare_resolved_invocation("places", {}), {}, lambda _: supplied
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
        name="bad",
        description="Bad result",
        output=OUTPUT,
        collection="example:places",
        unit_type="point",
    )
    def handler() -> object:
        return value

    plugin = PluginDefinition(location_operations=[handler])
    with pytest.raises((ValueError, TypeError), match="Location operation 'bad'"):
        plugin.invoke_operation(
            plugin.prepare_resolved_invocation("bad", {}), {}, Mock()
        )


def test_invalid_declarations_and_registration() -> None:
    with pytest.raises(PluginDefinitionError, match=r"Location operation 'missing'"):
        location_operation(
            name="missing",
            description="Missing",
            output=pa.DataFrameSchema({}),
            collection="example:places",
            unit_type="point",
        )(lambda: None)
    for handler in [lambda _unsupported: None, lambda *_args: None]:
        with pytest.raises(PluginDefinitionError, match="Location operation 'bad'"):
            location_operation(
                name="bad",
                description="Bad",
                output=OUTPUT,
                collection="example:places",
                unit_type="point",
            )(handler)

    async def asynchronous() -> None:
        await Mock()()

    with pytest.raises(
        PluginDefinitionError, match=r"Location operation 'bad'.*synchronous"
    ):
        location_operation(
            name="bad",
            description="Bad",
            output=OUTPUT,
            collection="example:places",
            unit_type="point",
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
        name="places",
        description="Places",
        output=OUTPUT,
        collection="example:places",
        unit_type="point",
    )
    def handler() -> gpd.GeoDataFrame:
        return producer_result(locations({"count": 1}), OUTPUT)

    plugin = PluginDefinition(location_operations=[handler])
    manifest = plugin.manifest(
        plugin=PluginInfo(name="places", version="1"), factory="places:create"
    )
    installed = InstalledPlugin("places", manifest, {})
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: [installed])
    catalog = registry.Catalog.load(Mock())
    assert catalog.entries == {}
    assert catalog.get_metrics_info() == []
    assert catalog.graph.nodes["places"].node.kind == "location_operation"
    assert build_graph({"places": manifest.nodes}).outputs == {}
    runner = LocalRunner.from_definitions({"places": plugin})
    with pytest.raises(PluginDefinitionError, match="Unknown public metric"):
        runner.run("places", {})
    with pytest.raises(PluginDefinitionError, match="not a metric"):
        plugin.invoke(plugin.prepare_resolved_invocation("places", {}), {}, Mock())
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
        name="places",
        description="Places",
        output=OUTPUT,
        collection="example:places",
        unit_type="point",
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
    monkeypatch.setattr(worker, "load_validated_plugin_definition", lambda _: plugin)
    runtime = Mock(plugins=config)
    runtime.get_worker.return_value.queues = ["default"]
    assert set(worker.load_runner_metric_entries("worker", config=runtime)) == {
        "counts"
    }


def test_resolver_context_and_dependency_validation() -> None:
    plugin = definition()
    invocation = plugin.prepare_resolved_invocation(
        "select_places", {"source": locations(), "parameters": {}}
    )
    with pytest.raises(PluginDefinitionError, match="dependency inputs"):
        plugin.invoke_operation(invocation, {}, Mock())
    with pytest.raises(
        PluginDefinitionError, match="context requires resolve_locations"
    ):
        plugin.invoke_operation(
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
        name="places",
        description="Places",
        output=schema,
        collection="example:places",
        unit_type="point",
    )
    def handler() -> gpd.GeoDataFrame:
        return producer_result(locations({}), schema)

    plugin = PluginDefinition(location_operations=[handler])
    assert (
        plugin.normalize_location_result(
            plugin.prepare_resolved_invocation("places", {}),
            producer_result(locations({}), schema),
        ).to_dict()
        == locations({}).to_dict()
    )
