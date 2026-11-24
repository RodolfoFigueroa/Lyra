"""Named handler location contracts and descriptive metadata."""

from typing import Annotated

import geopandas as gpd
import pandas as pd
import pytest
from geopandas import GeoDataFrame
from pydantic import Field, ValidationError

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import (
    NODE_MANIFEST_ADAPTER,
    PluginInfo,
    PluginManifest,
)
from lyra.runtime.arguments import prepare_dependency
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.runtime_inputs import assert_records, native_inputs
from test_support.table_fixtures import value_frame, value_schema
from tests.geometry_fixtures import projected_collection
from tests.provider_helpers import FakeProvider


def _locations(*kinds: str) -> NativeLocationInput:
    payload = projected_collection(*kinds)
    frame = gpd.GeoDataFrame.from_features(payload["features"], crs="EPSG:27700")
    frame["location_id"] = [feature["id"] for feature in payload["features"]]
    frame = frame[["location_id", "geometry", "label"]]
    return NativeLocationInput(
        frame, capture_provider("places", FakeProvider).contract.reference
    )


def test_named_locations_preserve_descriptions_and_independent_values() -> None:
    observed = []

    @metric(
        code_revision="1",
        inputs={
            key: LocationDependency(provider=FakeProvider.id)
            for key in ("blocks", "lots", "bounds")
        },
        name="named",
        description="Named inputs.",
        outputs={"named": value_schema()},
    )
    def named(
        blocks: Annotated[
            gpd.GeoDataFrame,
            Field(description="Census blocks; polygons with population."),
        ],
        lots: Annotated[
            gpd.GeoDataFrame, Field(description="Lots; points with parent_block_id.")
        ],
        bounds: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        observed.append((blocks, lots, bounds))
        return value_frame()

    plugin = PluginDefinition(metrics=[named], resources={"places": FakeProvider})
    binding = BoundPlugin(plugin)
    blocks = _locations("Polygon")
    lots = _locations("Point", "Point")
    invocation = binding.prepare_resolved_invocation(
        "named", native_inputs({"blocks": blocks, "lots": lots, "bounds": lots})
    )
    binding.invoke(
        invocation, {}, lambda _: pytest.fail("No context or acquisition needed")
    )
    actual_blocks, actual_lots, actual_bounds = observed[0]
    assert actual_blocks.location_id.tolist() == list(blocks.ids)
    assert (
        actual_lots.location_id.tolist()
        == actual_bounds.location_id.tolist()
        == list(lots.ids)
    )
    assert named(actual_blocks, actual_lots, actual_bounds).empty
    description = plugin.describe("named")
    manifest = plugin.manifest(
        plugin=PluginInfo(name="named", version="1"), factory="named:create"
    )
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert restored.nodes[0].request_schema == description.manifest.request_schema
    assert set(description.manifest.location_inputs) == {"blocks", "lots", "bounds"}
    properties = description.manifest.request_schema["properties"]
    assert (
        properties["blocks"]["description"]
        == "Census blocks; polygons with population."
    )
    assert properties["lots"]["description"] == "Lots; points with parent_block_id."
    assert {
        key: value
        for key, value in properties["bounds"].items()
        if key != "description"
    } == {
        key: value for key, value in properties["lots"].items() if key != "description"
    }


def test_forwarding_only_and_location_free_handlers() -> None:
    @metric(
        code_revision="1",
        name="source",
        description="No location.",
        outputs={"source": value_schema()},
    )
    def source() -> pd.DataFrame:
        return value_frame()

    @metric(
        code_revision="1",
        inputs={"lots": LocationDependency(provider=FakeProvider.id)},
        name="located",
        description="Uses lots.",
        outputs={"located": value_schema()},
    )
    def located(lots: gpd.GeoDataFrame) -> pd.DataFrame:
        return value_frame(len(lots))

    @metric(
        code_revision="1",
        name="consumer",
        description="Forwarding input.",
        outputs={"consumer": value_schema()},
        inputs={
            "lots": LocationDependency(provider=FakeProvider.id),
            "data": Dependency("located", producer="located"),
        },
    )
    def consumer(lots: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        del lots
        return data

    plugin = PluginDefinition(
        metrics=[source, located, consumer], resources={"places": FakeProvider}
    )
    binding = BoundPlugin(plugin)
    assert binding.prepare_resolved_invocation("source", {}).arguments() == {}
    assert set(plugin.describe("consumer").manifest.location_inputs) == {"lots"}
    registry = compile_live_catalog({"test": plugin}).graph
    lots = _locations("Point")
    invocation = binding.prepare_resolved_invocation(
        "consumer", native_inputs({"lots": lots})
    )
    prepared = prepare_dependency(
        invocation, registry.dependencies["consumer"]["data"], binding
    )
    assert set(prepared.arguments()) == {"lots"}
    assert_records(prepared.arguments()["lots"], lots)


@pytest.mark.parametrize("argument", ["parameters", "context"])
def test_location_collisions_are_actionable(argument: str) -> None:
    def parameters_handler(parameters: GeoDataFrame) -> None:
        del parameters

    def context_handler(context: GeoDataFrame) -> None:
        del context

    def data_handler(data: GeoDataFrame) -> None:
        del data

    handler = {
        "parameters": parameters_handler,
        "context": context_handler,
        "data": data_handler,
    }[argument]
    with pytest.raises(PluginDefinitionError, match=f"{argument}: unsupported"):
        metric(
            code_revision="1",
            name="collision",
            description="Collision.",
            outputs={"collision": value_schema()},
            inputs={"data": Dependency("source", producer="source")}
            if argument == "data"
            else {},
        )(handler)


@pytest.mark.parametrize(
    "annotation",
    [
        list[GeoDataFrame],
        Annotated[GeoDataFrame, "description"],
        GeoDataFrame | None,
        Annotated[GeoDataFrame, Field(alias="renamed")],
        Annotated[GeoDataFrame, Field(description=" ")],
    ],
)
def test_unsupported_location_annotations(annotation: object) -> None:
    def handler(lots: object) -> None:
        pass

    handler.__annotations__ = {"lots": annotation}
    with pytest.raises(PluginDefinitionError, match="lots:"):
        metric(
            code_revision="1",
            name="invalid",
            description="Invalid.",
            inputs={"lots": LocationDependency(provider=FakeProvider.id)},
            outputs={"invalid": value_schema()},
        )(handler)


def test_manifest_rejects_colliding_location_names() -> None:
    @metric(
        code_revision="1",
        name="source",
        inputs={"lots": LocationDependency(provider=FakeProvider.id)},
        description="Source.",
        outputs={"source": value_schema()},
    )
    def source(lots: gpd.GeoDataFrame) -> pd.DataFrame:
        return value_frame(len(lots))

    payload = (
        PluginDefinition(metrics=[source], resources={"places": FakeProvider})
        .node_manifests[0]
        .model_dump()
    )
    with pytest.raises(ValidationError, match="conflict"):
        NODE_MANIFEST_ADAPTER.validate_python(
            {**payload, "resources": {"lots": "database"}}
        )
