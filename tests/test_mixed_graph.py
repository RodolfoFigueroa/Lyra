"""Mixed SDK dependency graphs preserve binding paths and validate receiving inputs."""

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.parameters import use_key
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition, metric
from tests.live_graph_helpers import operation, plugins


def test_nested_cross_plugin_graph_preserves_owner_and_public_schema() -> None:
    binding = Dependency("value", producer="producer").bind(
        places=LocationDependency("select", parameters={"year": 2025}).bind(
            source=LocationDependency("select", parameters={"year": 2024}).bind(
                source=Input("seed")
            )
        )
    )
    definitions = plugins(binding, target="tracts")
    # A receiving metric still requires counties, so supply a canonical onward route.
    definitions["onward"] = PluginDefinition(
        location_operations=[operation("onward", "tracts", "counties")]
    )
    catalog = compile_live_catalog(definitions)
    graph = catalog.graph
    path = "inputs.first.locations.places"
    plan = prepare_execution(
        catalog,
        {"metric": "consumer", "inputs": {"seed": {"provider": "tracts", "ids": []}}},
    )
    assert plan.parameter_uses[use_key("consumer", plan.parameters, path)] == {
        "year": 2025
    }
    assert plan.parameter_uses[
        use_key("consumer", plan.parameters, path + ".locations.source")
    ] == {"year": 2024}
    outer = graph.location_dependencies["consumer"][path]
    inner = graph.location_dependencies["consumer"][path + ".locations.source"]
    assert outer.consumer == inner.consumer == graph.nodes["consumer"]
    assert outer.producer == inner.producer == graph.nodes["select"]
    assert graph.order.index("select") < graph.order.index("consumer")
    assert set(graph.nodes["consumer"].node.request_schema["properties"]) == {
        "seed",
        "parameters",
    }
    assert set(graph.nodes["producer"].node.request_schema["properties"]) == {
        "places",
        "parameters",
    }


@pytest.mark.parametrize(
    ("binding", "reason"),
    [
        (LocationDependency("missing"), "Unknown dependency producer"),
        (LocationDependency("producer"), "Expected location operation"),
        (LocationDependency("value"), "Unknown dependency producer"),
        (
            LocationDependency("select").bind(bad=Input("seed")),
            "Unknown receiving location",
        ),
        (
            LocationDependency("select").bind(source=Input("missing")),
            "undeclared input reference",
        ),
        (LocationDependency("select"), "No location supply"),
        (
            LocationDependency("select", parameters={"bad": 1}).bind(
                source=Input("seed")
            ),
            "Unknown receiving parameter",
        ),
        (
            LocationDependency(
                "select", parameters={"year": Input("parameters.missing")}
            ).bind(source=Input("seed")),
            "undeclared input reference",
        ),
    ],
)
def test_invalid_location_invocations(binding: LocationDependency, reason: str) -> None:
    with pytest.raises((ValueError, TypeError), match=reason):
        compile_live_catalog(
            plugins(Dependency("value", producer="producer").bind(places=binding))
        )


@pytest.mark.parametrize("name", ["select", "other"])
def test_operation_names_share_graph_namespace(name: str) -> None:
    @metric(
        name=name,
        description="Independent output",
        code_revision="1",
        outputs={"select": pa.DataFrameSchema(strict=True)},
    )
    def calculate() -> pd.DataFrame:
        return pd.DataFrame()

    definitions = plugins()
    definitions["extra"] = PluginDefinition(metrics=[calculate])
    if name == "select":
        with pytest.raises(ValueError, match="Duplicate node"):
            compile_live_catalog(definitions)
    else:
        assert (name, "select") in compile_live_catalog(definitions).graph.outputs
