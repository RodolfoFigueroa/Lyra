"""Mixed graph validation uses contracts without importing implementations."""

import importlib

import pytest
from lyra.sdk import MetricParameters
from lyra.sdk.dependencies import Dependency, Input, LocationDependency
from lyra.sdk.models.output import LocationOutputMetadata, OutputMetadata
from lyra.sdk.models.plugin import NodeManifest

from tests.test_plugin_graph import Defaulted, Renamed, Required, graph, node


def operation(
    name: str,
    *,
    inputs: dict[str, Dependency] | None = None,
    parameters: type[MetricParameters] | None = None,
    spatial: set[str] | None = None,
) -> NodeManifest:
    contract = node(name, inputs=inputs, parameters=parameters, spatial=spatial)
    return NodeManifest.model_validate(
        {
            **contract.model_dump(),
            "kind": "location_operation",
            "outputs": {},
            "location_output": LocationOutputMetadata(
                id_column="id", output=OutputMetadata(columns=[])
            ),
        }
    )


def test_nested_cross_plugin_graph_preserves_owner_and_public_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("Graph construction imported an implementation")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    root = node(
        "blocks",
        spatial={"blocks"},
        parameters=Renamed,
        inputs={
            "totals": Dependency(
                "street_totals",
                locations={
                    "streets": LocationDependency(
                        "select_streets",
                        locations={
                            "area": LocationDependency(
                                "buffer",
                                locations={"source": Input("blocks")},
                                parameters={"year": Input("parameters.vintage")},
                            )
                        },
                    )
                },
            )
        },
    )
    street = node("street_totals", spatial={"streets"})
    select = operation(
        "select_streets", spatial={"area"}, inputs={"data": Dependency("lookup")}
    )
    buffer = operation("buffer", spatial={"source"}, parameters=Required)
    result = graph(root, street, select, buffer, node("lookup"))
    edges = result.location_dependencies["blocks"]
    path = "inputs.totals.locations.streets"
    assert edges[path].producer.node.name == "select_streets"
    assert edges[path + ".locations.area"].consumer.node.name == "blocks"
    assert edges[path + ".locations.area"].producer.node.name == "buffer"
    assert (
        result.dependencies["select_streets"]["data"].output.producer.node.name
        == "lookup"
    )
    assert result.order.index("lookup") < result.order.index("select_streets")
    assert result.order.index("select_streets") < result.order.index("blocks")
    assert result.order.index("buffer") < result.order.index("blocks")
    root_properties = root.request_schema["properties"]
    street_properties = street.request_schema["properties"]
    assert isinstance(root_properties, dict)
    assert isinstance(street_properties, dict)
    assert set(root_properties) == {"blocks", "parameters"}
    assert set(street_properties) == {"streets"}


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        (LocationDependency("missing"), "no enabled producer"),
        (LocationDependency("street_totals"), "got a metric"),
        (LocationDependency("alias"), "got a table output"),
        (
            LocationDependency("select", locations={"bad": Input("blocks")}),
            "unknown location override",
        ),
        (
            LocationDependency("select", locations={"area": Input("unknown")}),
            "unknown consumer location",
        ),
        (LocationDependency("select"), "required spatial input 'area'"),
        (
            LocationDependency("buffer", parameters={"bad": 1}),
            "unknown parameter override",
        ),
        (
            LocationDependency("buffer", parameters={"year": Input("parameters.year")}),
            "unknown consumer parameter",
        ),
        (LocationDependency("buffer"), "required parameter 'year'"),
    ],
)
def test_invalid_location_invocations(
    binding: LocationDependency, expected: str
) -> None:
    root = node(
        "blocks",
        spatial={"blocks"},
        parameters=Renamed,
        inputs={"data": Dependency("street_totals", locations={"streets": binding})},
    )
    with pytest.raises(ValueError, match=expected) as error:
        graph(
            root,
            node("street_totals", spatial={"streets"}),
            node("table_node", outputs=("alias",)),
            operation("select", spatial={"area"}),
            operation("buffer", parameters=Required),
        )
    assert "Node 'blocks'" in str(error.value)
    assert "inputs.data.locations.streets" in str(error.value)


def test_operation_cannot_supply_table_dependency() -> None:
    with pytest.raises(ValueError, match=r"consumer.*data.*select.*expected a table"):
        graph(
            node("consumer", inputs={"data": Dependency("select")}), operation("select")
        )


def test_nested_references_cannot_use_intermediate_target_inputs() -> None:
    root = node(
        "root",
        spatial={"blocks"},
        parameters=Renamed,
        inputs={
            "data": Dependency(
                "table",
                locations={
                    "streets": LocationDependency(
                        "outer",
                        locations={
                            "area": LocationDependency(
                                "inner",
                                locations={"area": Input("area")},
                                parameters={"year": Input("parameters.year")},
                            )
                        },
                        parameters={"year": 2020},
                    )
                },
            )
        },
    )
    with pytest.raises(
        ValueError, match=r"unknown consumer parameter.*unknown consumer location"
    ) as error:
        graph(
            root,
            node("table", spatial={"streets"}),
            operation("outer", spatial={"area"}, parameters=Required),
            operation("inner", spatial={"area"}, parameters=Required),
        )
    assert "inputs.data.locations.streets.locations.area" in str(error.value)


def test_nested_matching_forwarding_and_defaults_use_owner() -> None:
    root = node(
        "root",
        spatial={"area"},
        parameters=Required,
        inputs={
            "data": Dependency(
                "table",
                locations={
                    "streets": LocationDependency(
                        "outer",
                        locations={"area": LocationDependency("inner")},
                    )
                },
            )
        },
    )
    assert graph(
        root,
        node("table", spatial={"streets"}),
        operation("outer", spatial={"area"}),
        operation("inner", spatial={"area"}, parameters=Required),
    )
    assert graph(
        node(
            "root",
            inputs={
                "data": Dependency(
                    "table", locations={"streets": LocationDependency("defaulted")}
                )
            },
        ),
        node("table", spatial={"streets"}),
        operation("defaulted", parameters=Defaulted),
    )


@pytest.mark.parametrize("cycle", ["self", "operation", "mixed", "nested"])
def test_location_cycles_include_receiving_paths(cycle: str) -> None:
    table = node("table", spatial={"area"})
    target = "first" if cycle == "self" else "second"
    binding = LocationDependency(target)
    if cycle == "nested":
        binding = LocationDependency("wrapper", locations={"area": binding})
    first = operation(
        "first", inputs={"data": Dependency("table", locations={"area": binding})}
    )
    second = operation(
        "second",
        inputs={
            "data": Dependency("table", locations={"area": LocationDependency("first")})
        },
    )
    if cycle == "mixed":
        second = operation("second", inputs={"data": Dependency("root")})
        root = node(
            "root",
            inputs={
                "data": Dependency(
                    "table", locations={"area": LocationDependency("first")}
                )
            },
        )
    else:
        root = node("root")
    with pytest.raises(ValueError, match="Dependency cycle") as error:
        graph(table, first, second, root, operation("wrapper", spatial={"area"}))
    message = str(error.value)
    assert "first" in message
    assert "inputs.data.locations.area" in message
    if cycle == "nested":
        assert "inputs.data.locations.area.locations.area" in message
    if cycle == "mixed":
        assert "root" in message
        assert "input 'data'" in message


def test_operations_own_their_table_and_nested_location_bindings() -> None:
    root = node(
        "root",
        inputs={
            "data": Dependency(
                "table",
                locations={
                    "area": LocationDependency("outer", parameters={"year": 2024})
                },
            )
        },
    )
    outer = operation(
        "outer",
        parameters=Required,
        inputs={
            "data": Dependency(
                "table",
                locations={
                    "area": LocationDependency(
                        "inner", parameters={"year": Input("parameters.year")}
                    )
                },
            )
        },
    )
    result = graph(
        root,
        outer,
        operation("inner", parameters=Required),
        node("table", spatial={"area"}),
    )
    edge = result.location_dependencies["outer"]["inputs.data.locations.area"]
    assert edge.consumer.node.name == "outer"
    assert edge.producer.node.name == "inner"
    assert (
        result.order.index("inner")
        < result.order.index("outer")
        < result.order.index("root")
    )


@pytest.mark.parametrize("conflict", ["node", "output"])
def test_operation_names_share_graph_namespace(conflict: str) -> None:
    other = node("select") if conflict == "node" else node("table", outputs=("select",))
    with pytest.raises(ValueError, match=r"Duplicate node|collides with another node"):
        graph(operation("select"), other)
