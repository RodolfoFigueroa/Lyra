"""Recursive invocation contracts remain local, serializable, and unambiguous."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas import GeoDataFrame
from pydantic import Field

from lyra.contracts.dependencies import Input, describe_dependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import PluginInfo
from lyra.sdk.dependencies import (
    Dependency,
    LocationDependency,
)
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import metric as native_metric
from test_support.declarations import PluginDefinition, location_operation, metric
from test_support.location_helpers import node_contract
from test_support.location_schema import column, contract, declaration
from test_support.plugin_graph import node


class Parameters(MetricParameters):
    year: int = Field(default=2025, description="Selection year")


def nested_binding(path: str = "lots") -> LocationDependency:
    return LocationDependency(
        "select_streets",
        parameters={
            "settings": {"kind": "location_dependency", "operation": "literal"},
            "nullable": None,
            "values": [True, 2, "three", {"kind": "input", "path": "missing"}],
        },
    ).bind(
        region=LocationDependency(
            "buffer_region",
            parameters={"year": Input("parameters.year"), "distance": 10},
        ).bind(source=Input(path)),
        context=Input("lots"),
    )


@pytest.mark.parametrize("kind", ["metric", "location_operation"])
def test_nested_bindings_roundtrip_and_public_boundary(kind: str) -> None:
    binding = nested_binding()
    inputs = {
        "table": Dependency("street_records", producer="street_records").bind(
            streets=binding
        )
    }

    def handler(
        lots: GeoDataFrame, *, parameters: Parameters, table: pd.DataFrame
    ) -> None:
        pytest.fail(f"Handler executed: {lots}, {parameters}, {table}")

    if kind == "metric":
        decorated = metric(
            locations={"lots": node_contract()},
            code_revision="1",
            name="owner",
            description="Owner",
            outputs={
                "records": pa.DataFrameSchema({"value": column(int)}, strict=True)
            },
            inputs=inputs,
        )(handler)
        plugin = PluginDefinition(metrics=[decorated])
    else:
        decorated = location_operation(
            locations={
                "lots": node_contract("test:units", "lot", contract(declaration()))
            },
            output_contract=node_contract("test:units", "lot", contract(declaration())),
            code_revision="1",
            name="owner",
            description="Owner",
            output=contract(declaration()),
            inputs=inputs,
        )(handler)
        plugin = PluginDefinition(location_operations=[decorated])
    manifest = plugin.manifest(
        plugin=PluginInfo(name="bindings", version="0.0.0"), factory="example:plugin"
    )
    node = manifest.nodes[0]
    properties = node.request_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"lots", "parameters"}
    invocation = node.inputs["table"].locations["streets"]
    assert isinstance(invocation, LocationDependency)
    assert invocation == binding
    assert invocation.parameters["settings"] == binding.parameters["settings"]


def test_nested_references_belong_to_owner() -> None:
    def handler(
        lots: gpd.GeoDataFrame, *, parameters: Parameters, table: pd.DataFrame
    ) -> None:
        del lots, parameters, table

    with pytest.raises(
        PluginDefinitionError,
        match=r"streets.*region.*locations.source: undeclared input reference 'region'",
    ):
        native_metric(
            code_revision="1",
            name="owner",
            description="Owner",
            outputs={
                "records": pa.DataFrameSchema({"value": column(int)}, strict=True)
            },
            inputs={
                "lots": LocationDependency(provider="lots"),
                "table": Dependency(
                    "street_records",
                    producer="street_records",
                ).bind(streets=nested_binding("region")),
            },
        )(handler)


def test_nested_parameter_reference_identifies_receiver() -> None:
    def handler(
        lots: gpd.GeoDataFrame, *, parameters: Parameters, table: pd.DataFrame
    ) -> None:
        del lots, parameters, table

    with pytest.raises(
        PluginDefinitionError,
        match=r"locations.streets.*parameters.year: .*'parameters.missing'",
    ):
        native_metric(
            code_revision="1",
            name="owner",
            description="Owner",
            outputs={
                "records": pa.DataFrameSchema({"value": column(int)}, strict=True)
            },
            inputs={
                "lots": LocationDependency(provider="lots"),
                "table": Dependency(
                    "street_records",
                    producer="street_records",
                ).bind(
                    streets=LocationDependency(
                        "select_streets",
                        parameters={"year": Input("parameters.missing")},
                    )
                ),
            },
        )(handler)


@pytest.mark.parametrize("declaration_type", [Dependency, LocationDependency])
@pytest.mark.parametrize(
    ("bindings", "receiving"),
    [
        ({"locations": {"region": Input("parameters.year")}}, "locations.region"),
        ({"parameters": {"year": Input("lots")}}, "parameters.year"),
        (
            {"locations": {"region": Dependency("table", producer="table")}},
            "locations.region",
        ),
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
    def construct() -> None:
        declaration = (
            Dependency(
                "source", producer="source", parameters=bindings.get("parameters", {})
            )
            if declaration_type is Dependency
            else LocationDependency("source", parameters=bindings.get("parameters", {}))
        )
        declaration.bind(**bindings.get("locations", {}))

    with pytest.raises((ValueError, TypeError), match=receiving):
        construct()


@pytest.mark.parametrize("name", ["", "Bad", "has-dash", "a.b"])
def test_operation_names_validate_locally(name: str) -> None:
    with pytest.raises(ValueError, match="operation"):
        LocationDependency(operation=name)


@pytest.mark.parametrize("operation", [False, True])
def test_chained_bindings_capture_independent_values(*, operation: bool) -> None:
    literal = {"nested": [{"kind": "input", "path": "parameters.year"}]}
    original = (
        LocationDependency(operation="source", parameters={"settings": literal})
        if operation
        else Dependency(
            producer="source", output="records", parameters={"settings": literal}
        )
    )
    nested = LocationDependency(
        operation="select", parameters={"settings": literal}
    ).bind(source=Input("lots"))
    first = original.bind(source=nested, context=Input("lots"))
    second = first.bind(context=Input("other"))
    literal["nested"].clear()
    nested.parameters.clear()
    first.parameters.clear()
    first.locations.clear()
    assert original.locations == {}
    assert second.locations["context"] == Input("other")
    retained = second.locations["source"]
    assert isinstance(retained, LocationDependency)
    expected = {"nested": [{"kind": "input", "path": "parameters.year"}]}
    assert retained.parameters == {"settings": expected}
    assert original.parameters == second.parameters == {"settings": expected}
    assert retained.locations == {"source": Input("lots")}


def test_binding_validates_nested_mutations_before_capture() -> None:
    nested = LocationDependency(operation="select")
    nested.parameters["bad.field"] = 1
    with pytest.raises(ValueError, match=r"parameters.bad.field"):
        Dependency(producer="source", output="records").bind(source=nested)


def test_inspection_descriptions_cannot_become_executable_inputs() -> None:
    declaration = node(
        "consumer", inputs={"data": Dependency(producer="source", output="records")}
    )
    with pytest.raises(TypeError, match="live Dependency"):
        type(declaration).model_validate(declaration.model_dump(mode="json"))


def test_inspection_distinguishes_reference_looking_literals() -> None:
    literal = {"kind": "input", "path": "parameters.year"}
    dependency = Dependency(
        producer="source",
        output="records",
        parameters={
            "settings": literal,
            "year": Input("parameters.year"),
        },
    )
    view = describe_dependency(dependency)
    assert view["parameters"]["settings"] == {"kind": "literal", "value": literal}
    assert view["parameters"]["year"] == literal
    view["parameters"]["settings"]["value"]["path"] = "changed"
    assert dependency.parameters["settings"] == literal
