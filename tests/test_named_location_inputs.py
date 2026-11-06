"""Named handler location contracts and descriptive metadata."""

from typing import Annotated

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field, ValidationError

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import (
    NODE_MANIFEST_ADAPTER,
    PluginInfo,
    PluginManifest,
)
from lyra.data.locations import Locations
from lyra.runtime.arguments import prepare_dependency
from lyra.runtime.invocation import BoundPlugin
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    scoped_payload,
)
from test_support.plugin_graph import graph
from tests.geometry_fixtures import projected_collection


def test_named_locations_preserve_descriptions_and_independent_values() -> None:
    observed = []

    @metric(
        locations={
            "blocks": node_contract(),
            "lots": node_contract(),
            "bounds": node_contract(),
        },
        name="named",
        description="Named inputs.",
        outputs={"named": pa.DataFrameSchema({})},
    )
    def named(
        blocks: Annotated[
            Locations, Field(description="Census blocks; polygons with population.")
        ],
        lots: Annotated[
            Locations, Field(description="Lots; points with parent_block_id.")
        ],
        bounds: Locations,
    ) -> pd.DataFrame:
        observed.append((blocks, lots, bounds))
        return pd.DataFrame()

    plugin = PluginDefinition(metrics=[named])
    binding = BoundPlugin(plugin)
    blocks = fixture_locations(
        scoped_payload(projected_collection("Polygon")), schema=output_schema(label=str)
    )
    lots = fixture_locations(
        scoped_payload(projected_collection("Point", "Point")),
        schema=output_schema(label=str),
    )
    invocation = binding.prepare_resolved_invocation(
        "named", {"blocks": blocks, "lots": lots, "bounds": lots}
    )
    binding.invoke(
        invocation, {}, lambda _: pytest.fail("No context or acquisition needed")
    )
    assert observed == [(blocks, lots, lots)]
    assert named(blocks, lots, lots).empty
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
        name="source",
        description="No location.",
        outputs={"source": pa.DataFrameSchema({})},
    )
    def source() -> pd.DataFrame:
        return pd.DataFrame()

    @metric(
        locations={"lots": node_contract()},
        name="located",
        description="Uses lots.",
        outputs={"located": pa.DataFrameSchema({})},
    )
    def located(lots: Locations) -> pd.DataFrame:
        return pd.DataFrame(index=list(lots.ids))

    @metric(
        locations={"lots": node_contract()},
        name="consumer",
        description="Forwarding input.",
        outputs={"consumer": pa.DataFrameSchema({})},
        inputs={"data": Dependency("located", producer="located")},
    )
    def consumer(lots: Locations, data: pd.DataFrame) -> pd.DataFrame:
        del lots
        return data

    plugin = PluginDefinition(metrics=[source, located, consumer])
    binding = BoundPlugin(plugin)
    assert binding.prepare_resolved_invocation("source", {}).arguments() == {}
    assert set(plugin.describe("consumer").manifest.location_inputs) == {"lots"}
    registry = graph(*plugin.node_manifests)
    lots = fixture_locations(
        scoped_payload(projected_collection("Point")), schema=output_schema(label=str)
    )
    invocation = binding.prepare_resolved_invocation("consumer", {"lots": lots})
    prepared = prepare_dependency(
        invocation, registry.dependencies["consumer"]["data"], binding
    )
    assert prepared.arguments() == {"lots": lots}


@pytest.mark.parametrize("argument", ["parameters", "context", "data"])
def test_location_collisions_are_actionable(argument: str) -> None:
    def parameters_handler(parameters: Locations) -> None:
        del parameters

    def context_handler(context: Locations) -> None:
        del context

    def data_handler(data: Locations) -> None:
        del data

    handler = {
        "parameters": parameters_handler,
        "context": context_handler,
        "data": data_handler,
    }[argument]
    with pytest.raises(
        PluginDefinitionError, match=f"{argument}: location argument conflicts"
    ):
        metric(
            name="collision",
            description="Collision.",
            outputs={"collision": pa.DataFrameSchema({})},
            inputs={"data": Dependency("source", producer="source")}
            if argument == "data"
            else None,
        )(handler)


@pytest.mark.parametrize(
    "annotation",
    [
        list[Locations],
        Annotated[Locations, "description"],
        Locations | None,
        Annotated[Locations, Field(alias="renamed")],
        Annotated[Locations, Field(description=" ")],
    ],
)
def test_unsupported_location_annotations(annotation: object) -> None:
    def handler(lots: object) -> None:
        pass

    handler.__annotations__ = {"lots": annotation}
    with pytest.raises(PluginDefinitionError, match="lots:"):
        metric(
            name="invalid",
            description="Invalid.",
            outputs={"invalid": pa.DataFrameSchema({})},
        )(handler)


def test_manifest_rejects_bounds_kind_and_colliding_location_names() -> None:
    @metric(
        locations={"lots": node_contract()},
        name="source",
        description="Source.",
        outputs={"source": pa.DataFrameSchema({})},
    )
    def source(lots: Locations) -> pd.DataFrame:
        return pd.DataFrame(index=list(lots.ids))

    payload = PluginDefinition(metrics=[source]).node_manifests[0].model_dump()
    with pytest.raises(ValidationError, match="spatial_inputs"):
        NODE_MANIFEST_ADAPTER.validate_python(
            {**payload, "spatial_inputs": {"lots": "bounds"}}
        )
    with pytest.raises(ValidationError, match="conflict"):
        NODE_MANIFEST_ADAPTER.validate_python(
            {**payload, "resources": {"lots": "database"}}
        )
