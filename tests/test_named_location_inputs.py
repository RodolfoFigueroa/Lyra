"""Named handler location contracts and descriptive metadata."""

from typing import Annotated

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    Locations,
    PluginDefinition,
    PluginDefinitionError,
    metric,
)
from lyra.sdk.arguments import prepare_dependency
from lyra.sdk.models.plugin import NodeManifest, PluginInfo, PluginManifest
from pydantic import Field, ValidationError

from tests.location_helpers import scoped_payload
from tests.test_plugin_graph import graph
from tests.test_spatial_geometry_contract import _collection


def test_named_locations_preserve_descriptions_and_independent_values() -> None:
    observed = []

    @metric(
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
    blocks = Locations.from_dict(scoped_payload(_collection("Polygon")))
    lots = Locations.from_dict(scoped_payload(_collection("Point", "Point")))
    invocation = plugin.prepare_invocation(
        "named", {"blocks": blocks, "lots": lots, "bounds": lots}
    )
    plugin.invoke(
        invocation, {}, lambda _: pytest.fail("No context or acquisition needed")
    )
    assert observed == [(blocks, lots, lots)]
    assert named(blocks, lots, lots).empty
    description = plugin.describe("named")
    manifest = plugin.manifest(
        plugin=PluginInfo(name="named", version="1"), factory="named:create"
    )
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert restored.nodes[0].request_schema == description.request_schema
    assert description.spatial_inputs == dict.fromkeys(
        ["blocks", "lots", "bounds"], "location"
    )
    properties = description.request_schema["properties"]
    assert (
        properties["blocks"]["description"]
        == "Census blocks; polygons with population."
    )
    assert properties["lots"]["description"] == "Lots; points with parent_block_id."
    assert properties["bounds"]["$ref"] == properties["lots"]["$ref"]


def test_forwarding_only_and_location_free_handlers() -> None:
    @metric(
        name="source",
        description="No location.",
        outputs={"source": pa.DataFrameSchema({})},
    )
    def source() -> pd.DataFrame:
        return pd.DataFrame()

    @metric(
        name="located",
        description="Uses lots.",
        outputs={"located": pa.DataFrameSchema({})},
    )
    def located(lots: Locations) -> pd.DataFrame:
        return pd.DataFrame(index=list(lots.ids))

    @metric(
        name="consumer",
        description="Forwarding input.",
        outputs={"consumer": pa.DataFrameSchema({})},
        inputs={"data": Dependency("located")},
    )
    def consumer(lots: Locations, data: pd.DataFrame) -> pd.DataFrame:
        del lots
        return data

    plugin = PluginDefinition(metrics=[source, located, consumer])
    assert plugin.prepare_invocation("source", {}).arguments() == {}
    assert plugin.describe("consumer").spatial_inputs == {"lots": "location"}
    registry = graph(*plugin.node_manifests)
    lots = Locations.from_dict(scoped_payload(_collection("Point")))
    invocation = plugin.prepare_invocation("consumer", {"lots": lots})
    prepared = prepare_dependency(
        invocation, registry.dependencies["consumer"]["data"], plugin
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
            inputs={"data": Dependency("source")} if argument == "data" else None,
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
        name="source", description="Source.", outputs={"source": pa.DataFrameSchema({})}
    )
    def source(lots: Locations) -> pd.DataFrame:
        return pd.DataFrame(index=list(lots.ids))

    payload = PluginDefinition(metrics=[source]).node_manifests[0].model_dump()
    with pytest.raises(ValidationError, match="location"):
        NodeManifest.model_validate({**payload, "spatial_inputs": {"lots": "bounds"}})
    with pytest.raises(ValidationError, match="conflict"):
        NodeManifest.model_validate({**payload, "resources": {"lots": "database"}})
