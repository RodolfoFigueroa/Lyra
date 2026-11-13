"""Live graph compilation remains service-free and preserves binding sites."""

from unittest.mock import Mock

import pytest

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from tests.live_graph_helpers import plugins
from tests.node_declaration_helpers import CENSUS, Tracts


def test_live_cross_plugin_graph_and_description_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("resource acquired"))
    monkeypatch.setattr(Tracts, "__init__", forbidden)
    definitions = plugins()
    catalog = compile_live_catalog(definitions)
    graph = catalog.graph
    assert set(graph.dependency_closure(["consumer"])) == {
        "consumer",
        "producer",
        "select",
    }
    edges = graph.dependencies["consumer"]
    assert edges["first"].dependency.parameters == {"year": 2020}
    assert edges["second"].dependency.parameters == {"year": 2021}
    for key in ("first", "second"):
        edge = graph.location_dependencies["consumer"][f"inputs.{key}.locations.places"]
        assert edge.consumer.node.name == "consumer"
        assert edge.dependency.locations == {"source": Input("seed")}
        assert edge.dependency.parameters == {"year": Input("parameters.year")}
    producer = graph.nodes["producer"].node
    assert producer.kind == "metric"
    assert producer.origins == frozenset({CENSUS})
    discovery = Discovery(catalog)
    assert [item.name for item in discovery.search()] == ["consumer"]
    with pytest.raises(KeyError):
        discovery.inspect("producer")
    assert set(discovery.inspect("consumer").outputs) == {"result"}
    assert catalog.provider_registry["tracts"].scope.origins == frozenset({CENSUS})
    exposed = catalog.manifests["downstream"].nodes[0]
    exposed.inputs["first"].parameters.clear()
    exposed.inputs.clear()
    assert edges["first"].dependency.parameters == {"year": 2020}
    assert definitions["downstream"].declarations[0].inputs["first"].parameters == {
        "year": 2020
    }
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    ("binding", "error"),
    [
        (Dependency("value", producer="missing"), "Unknown dependency"),
        (Dependency("missing", producer="producer"), "Unknown metric output"),
        (Dependency("value", producer="select"), "Unknown metric output"),
        (
            Dependency("value", producer="producer").bind(unknown=Input("seed")),
            "Unknown receiving",
        ),
        (
            Dependency("value", producer="producer").bind(places=Input("missing")),
            "undeclared input reference",
        ),
        (
            Dependency("value", producer="producer", parameters={"bad": 1}).bind(
                places=Input("seed")
            ),
            "Unknown receiving parameter",
        ),
        (
            Dependency(
                "value",
                producer="producer",
                parameters={"year": Input("parameters.bad")},
            ).bind(places=Input("seed")),
            "undeclared input reference",
        ),
        (Dependency("result", producer="consumer").bind(seed=Input("seed")), "cycle"),
    ],
)
def test_invalid_live_graphs_fail_structurally(
    binding: Dependency,
    error: str,
) -> None:
    with pytest.raises((ValueError, TypeError), match=error):
        compile_live_catalog(plugins(binding))


def test_differing_source_provider_requires_a_canonical_route() -> None:
    with pytest.raises(ValueError, match="No canonical route"):
        compile_live_catalog(
            plugins(Dependency("value", producer="producer").bind(places=Input("seed")))
        )


@pytest.mark.parametrize("case", ["target", "provider", "input_cycle"])
def test_invalid_live_locations(case: str) -> None:
    if case == "target":
        definitions = plugins(target="missing")
    elif case == "provider":
        definitions = plugins(seed=LocationDependency(provider="missing"))
    else:
        definitions = plugins(
            seed=LocationDependency("select").bind(source=Input("seed"))
        )
    with pytest.raises(ValueError, match=r"Unknown provider|Location input cycle"):
        compile_live_catalog(definitions)
