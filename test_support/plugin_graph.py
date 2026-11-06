"""Reusable plugin graph fixtures."""

from pydantic import Field

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    NodeManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.runtime.planning.graph import PluginGraph, build_graph
from lyra.sdk.dependencies import Dependency, dependency_manifest
from lyra.sdk.parameters import MetricParameters, build_parameter_schema
from test_support.location_helpers import node_contract


class Required(MetricParameters):
    year: int | None = Field(description="Required even when nullable.")


class Defaulted(MetricParameters):
    year: int | None = Field(default=None, description="Default year.")


class Renamed(MetricParameters):
    vintage: int = Field(default=2020, description="Consumer year.")


class Nested(MetricParameters):
    settings: Required = Field(description="Nested settings.")


def node(
    name: str,
    *,
    outputs: tuple[str, ...] | None = None,
    inputs: dict[str, Dependency] | None = None,
    parameters: type[MetricParameters] | None = None,
    spatial: set[str] | None = None,
) -> MetricManifest:
    return MetricManifest(
        name=name,
        description=name,
        parameters_schema=build_parameter_schema(name, parameters),
        location_inputs={
            key: LocationInputDeclaration(contract=node_contract())
            for key in spatial or ()
        },
        inputs={key: dependency_manifest(dep) for key, dep in (inputs or {}).items()},
        outputs={key: OutputMetadata(columns=[]) for key in outputs or (name,)},
    )


def manifest(name: str, *nodes: NodeManifest) -> PluginManifest:
    return PluginManifest(
        plugin=PluginInfo(name=name, version="1"),
        factory="unavailable:create_plugin",
        nodes=list(nodes),
    )


def graph(*nodes: NodeManifest) -> PluginGraph:
    return build_graph(
        {
            f"plugin-{index}": manifest(f"plugin-{index}", item).nodes
            for index, item in enumerate(nodes)
        }
    )


def operation(
    name: str,
    *,
    inputs: dict[str, Dependency] | None = None,
    parameters: type[MetricParameters] | None = None,
    spatial: set[str] | None = None,
) -> LocationOperationManifest:
    contract = node(name, inputs=inputs, parameters=parameters, spatial=spatial)
    return LocationOperationManifest(
        **contract.model_dump(
            exclude={
                "kind",
                "outputs",
                "private",
                "family",
                "edition",
                "edition_metadata",
            }
        ),
        output_contract=node_contract(),
    )
