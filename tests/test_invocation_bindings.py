"""Recursive invocation contracts remain local, serializable, and unambiguous."""

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    Locations,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    location_operation,
    metric,
)
from lyra.sdk.dependencies import (
    DependencyManifest,
    LiteralArgument,
    LocationDependencyManifest,
)
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from pydantic import Field

from tests.test_location_schema import column, contract, declaration


class Parameters(MetricParameters):
    year: int = Field(default=2025, description="Selection year")


def nested_binding(path: str = "lots") -> LocationDependency:
    return LocationDependency(
        "select_streets",
        locations={
            "region": LocationDependency(
                "buffer_region",
                locations={"source": Input(path)},
                parameters={"year": Input("parameters.year"), "distance": 10},
            ),
            "context": Input("lots"),
        },
        parameters={
            "settings": {"kind": "location_dependency", "operation": "literal"},
            "nullable": None,
            "values": [True, 2, "three", {"kind": "input", "path": "missing"}],
        },
    )


@pytest.mark.parametrize("kind", ["metric", "location_operation"])
def test_nested_bindings_roundtrip_and_public_boundary(kind: str) -> None:
    binding = nested_binding()
    inputs = {"table": Dependency("street_records", locations={"streets": binding})}

    def handler(lots: Locations, parameters: Parameters, table: pd.DataFrame) -> None:
        pytest.fail(f"Handler executed: {lots}, {parameters}, {table}")

    if kind == "metric":
        decorated = metric(
            name="owner",
            description="Owner",
            outputs={"records": pa.DataFrameSchema({"value": column(int)})},
            inputs=inputs,
        )(handler)
        plugin = PluginDefinition(metrics=[decorated])
    else:
        decorated = location_operation(
            name="owner",
            description="Owner",
            output=contract(declaration()),
            inputs=inputs,
            collection="test:units",
            unit_type="lot",
        )(handler)
        plugin = PluginDefinition(location_operations=[decorated])
    manifest = plugin.manifest(
        plugin=PluginInfo(name="bindings", version="0.0.0"), factory="example:plugin"
    )
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    node = restored.nodes[0]
    properties = node.request_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"lots", "parameters"}
    invocation = node.inputs["table"].locations["streets"]
    assert isinstance(invocation, LocationDependencyManifest)
    assert invocation.to_declaration() == binding
    assert isinstance(invocation.parameters["settings"], LiteralArgument)
    assert invocation.parameters["settings"].value == binding.parameters["settings"]


def test_nested_references_belong_to_owner() -> None:
    def handler(lots: Locations, parameters: Parameters, table: pd.DataFrame) -> None:
        del lots, parameters, table

    with pytest.raises(
        PluginDefinitionError,
        match=r"streets.*region.*locations.source: undeclared input reference 'region'",
    ):
        metric(
            name="owner",
            description="Owner",
            outputs={"records": pa.DataFrameSchema({"value": column(int)})},
            inputs={
                "table": Dependency(
                    "street_records", locations={"streets": nested_binding("region")}
                )
            },
        )(handler)


def test_nested_parameter_reference_identifies_receiver() -> None:
    def handler(lots: Locations, parameters: Parameters, table: pd.DataFrame) -> None:
        del lots, parameters, table

    with pytest.raises(
        PluginDefinitionError,
        match=r"locations.streets.*parameters.year: .*'parameters.missing'",
    ):
        metric(
            name="owner",
            description="Owner",
            outputs={"records": pa.DataFrameSchema({"value": column(int)})},
            inputs={
                "table": Dependency(
                    "street_records",
                    locations={
                        "streets": LocationDependency(
                            "select_streets",
                            parameters={"year": Input("parameters.missing")},
                        )
                    },
                )
            },
        )(handler)


@pytest.mark.parametrize("declaration_type", [Dependency, LocationDependency])
@pytest.mark.parametrize(
    ("bindings", "receiving"),
    [
        ({"locations": {"region": Input("parameters.year")}}, "locations.region"),
        ({"parameters": {"year": Input("lots")}}, "parameters.year"),
        ({"locations": {"region": Dependency("table")}}, "locations.region"),
        (
            {"parameters": {"settings": LocationDependency("select")}},
            "parameters.settings",
        ),
        ({"parameters": {"nested.field": 1}}, "parameters.nested.field"),
        ({"locations": {"region": {"operation": "select"}}}, "locations.region"),
        ({"parameters": {"year": float("nan")}}, "parameters.year"),
    ],
)
def test_malformed_bindings_identify_receiver(
    declaration_type: type[Dependency] | type[LocationDependency],
    bindings: dict,
    receiving: str,
) -> None:
    with pytest.raises((ValueError, TypeError), match=receiving):
        declaration_type("source", **bindings)


def test_nested_manifest_validation_identifies_receiver() -> None:
    manifest = DependencyManifest.from_declaration(
        Dependency("records", locations={"streets": nested_binding()})
    ).model_dump()
    manifest["locations"]["streets"]["locations"]["region"]["parameters"]["year"] = {
        "kind": "input",
        "path": "lots",
    }
    with pytest.raises(ValueError, match=r"parameters.year.*parameter reference"):
        DependencyManifest.model_validate(manifest)


@pytest.mark.parametrize("name", ["", "Bad", "has-dash", "a.b"])
def test_operation_names_validate_locally(name: str) -> None:
    with pytest.raises(ValueError, match="operation"):
        LocationDependency(operation=name)
