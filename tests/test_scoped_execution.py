"""Scoped preparation exercised independently of public request integration."""

from pathlib import Path

import pandas as pd
import pytest
from lyra.sdk import Dependency, Input, Locations, MetricInputError, metric
from lyra.sdk.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.sdk.cache import TableCache

from tests.location_helpers import fixture_locations, output_schema, scoped_payload
from tests.test_graph_arguments import Consumer, Producer, geometry, plugin
from tests.test_graph_execution import TABLE, executor
from tests.test_plugin_graph import graph, node


def resolved() -> Locations:
    return fixture_locations(
        scoped_payload(geometry())
        | {"collection": "example:areas", "unit_type": "block"},
        schema=output_schema(label=str),
    )


@pytest.mark.parametrize(
    "change",
    ["collection", "unit_type", "id", "geometry", "properties", "crs", "parameters"],
)
def test_scoped_identity_includes_all_effective_inputs(change: str) -> None:
    first = resolved()
    payload = first.to_dict()
    parameters = Producer()
    match change:
        case "collection":
            payload["collection"] = "other:areas"
        case "unit_type":
            payload["unit_type"] = "lot"
        case "id":
            payload["features"][0]["id"] = "another"
        case "geometry":
            payload["features"][0]["geometry"]["coordinates"][0].reverse()
        case "properties":
            payload["features"][0]["properties"]["label"] = "changed"
        case "crs":
            payload["crs"]["properties"]["name"] = "EPSG:4326"
        case "parameters":
            parameters = Producer(year=1990)
    before = PreparedResolvedInvocation("source", Producer(), {"location": first})
    after = PreparedResolvedInvocation(
        "source",
        parameters,
        {
            "location": fixture_locations(
                scoped_payload(payload), schema=output_schema(label=str)
            )
        },
    )
    assert before.identity != after.identity
    assert TableCache.key(before.identity) != TableCache.key(after.identity)
    assert (
        before.identity
        == PreparedResolvedInvocation(
            "source",
            Producer(),
            {
                "location": fixture_locations(
                    scoped_payload(first.to_dict()), schema=output_schema(label=str)
                )
            },
        ).identity
    )


def test_scoped_preparation_rejects_unscoped_and_missing_inputs() -> None:
    implementation = plugin(None, spatial=True)
    with pytest.raises(MetricInputError, match="location"):
        implementation.prepare_resolved_invocation(
            "producer",
            {
                "location": {k: v for k, v in geometry().items() if k != "collection"},
                "bounds": resolved(),
            },
        )
    with pytest.raises(MetricInputError, match="expected fields"):
        implementation.prepare_resolved_invocation("producer", {"location": resolved()})


@pytest.mark.parametrize("persistent", [False, True])
def test_bindings_retain_scope_through_chain_and_reuse(
    tmp_path: Path, *, persistent: bool
) -> None:
    received: list[tuple[Locations, int]] = []
    free_calls: list[str] = []

    @metric(name="free", description="Free", outputs={"free": TABLE})
    def free() -> pd.DataFrame:
        free_calls.append("free")
        return pd.DataFrame()

    @metric(
        name="source",
        description="Source",
        outputs={"source": TABLE},
        inputs={"support": Dependency("free")},
    )
    def source(
        location: Locations, parameters: Producer, support: pd.DataFrame
    ) -> pd.DataFrame:
        assert isinstance(location, Locations)
        assert parameters.count == 2
        received.append((location, parameters.year))
        return support

    @metric(
        name="middle",
        description="Middle",
        outputs={"middle": TABLE},
        inputs={
            "data": Dependency("source", locations={"location": Input("lots")}),
            "other": Dependency("source", locations={"location": Input("ignored")}),
        },
    )
    def middle(
        lots: Locations,
        ignored: Locations,
        parameters: Consumer,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(lots, Locations)
        assert isinstance(ignored, Locations)
        pd.testing.assert_frame_equal(data, other)
        assert parameters.year == 2010
        return data

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            "a": Dependency(
                "middle",
                locations={"lots": Input("blocks"), "ignored": Input("blocks")},
                parameters={
                    "year": Input("parameters.baseline"),
                    "unrelated": Input("parameters.unrelated"),
                },
            ),
            "b": Dependency(
                "middle",
                locations={
                    "lots": Input("blocks"),
                    "ignored": Input("different"),
                },
                parameters={
                    "year": Input("parameters.baseline"),
                    "unrelated": "b",
                },
            ),
        },
    )
    def root(
        *,
        blocks: Locations,
        different: Locations,
        parameters: Consumer,
        a: pd.DataFrame,
        b: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(blocks, Locations)
        assert isinstance(different, Locations)
        assert parameters.baseline == 2010
        pd.testing.assert_frame_equal(a, b)
        return a

    runner, implementation = executor(root, middle, source, free)
    if persistent:
        runner.cache = TableCache(tmp_path)
    shared = resolved()
    different = fixture_locations(
        shared.to_dict() | {"unit_type": "lot"}, schema=output_schema(label=str)
    )
    snapshot = shared.to_dict()
    inputs = {
        "blocks": shared,
        "different": different,
        "parameters": {},
    }
    invocation = implementation.prepare_resolved_invocation("root", inputs)
    edge = runner.graph.dependencies["root"]["a"]
    middle_input = prepare_dependency(invocation, edge, runner.plugins["plugin_1"])
    source_input = prepare_dependency(
        middle_input,
        runner.graph.dependencies["middle"]["data"],
        runner.plugins["plugin_2"],
    )
    assert source_input.locations == {"location": shared}
    assert source_input.locations["location"] == shared
    assert source_input.arguments()["location"] == shared
    assert source_input.parameters == Producer(year=2010)
    runner.execute(invocation)
    assert received == [(shared, 2010), (different, 2010)]
    assert free_calls == ["free"]
    assert shared.to_dict() == snapshot

    # Root and middle miss cache on consumer-only data; source inputs stay equal.
    inputs["parameters"] = {"unrelated": "next job"}
    runner.execute(implementation.prepare_resolved_invocation("root", inputs))
    assert len(received) == (2 if persistent else 4)
    assert len(free_calls) == (1 if persistent else 2)
    if persistent:
        assert runner.cache is not None
        source_key = runner.persistent_key(source_input.identity)
        assert runner.cache.read(source_key) is not None


def test_automatic_scoped_forwarding_and_producer_defaults() -> None:
    @metric(name="source", description="Source", outputs={"source": TABLE})
    def source(location: Locations, parameters: Producer) -> pd.DataFrame:
        assert isinstance(location, Locations)
        assert parameters == Producer()
        return pd.DataFrame()

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={"data": Dependency("source")},
    )
    def root(location: Locations, data: pd.DataFrame) -> pd.DataFrame:
        assert isinstance(location, Locations)
        return data

    runner, implementation = executor(root, source)
    location = resolved()
    invocation = implementation.prepare_resolved_invocation(
        "root", {"location": location}
    )
    prepared = prepare_dependency(
        invocation,
        runner.graph.dependencies["root"]["data"],
        runner.plugins["plugin_1"],
    )
    assert prepared.locations["location"] == location
    assert prepared.parameters == Producer()
    runner.execute(invocation)


def test_binding_precedes_matching_name_and_aliases_do_not_change_identity() -> None:
    registry = graph(
        node(
            "consumer",
            spatial={"location", "bounds", "alias"},
            inputs={
                "data": Dependency(
                    "producer",
                    locations={"location": Input("alias")},
                )
            },
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    selected = resolved()
    other = fixture_locations(
        selected.to_dict() | {"collection": "other:areas"},
        schema=output_schema(label=str),
    )
    consumer = PreparedResolvedInvocation(
        "consumer", None, {"location": other, "bounds": selected, "alias": selected}
    )
    implementation = plugin(None, spatial=True)
    edge = registry.dependencies["consumer"]["data"]
    prepared = prepare_dependency(consumer, edge, implementation)
    direct = implementation.prepare_resolved_invocation(
        "producer", {"location": selected, "bounds": selected}
    )
    assert prepared.identity == direct.identity
    assert prepared.locations["location"] == selected
    assert consumer.locations["location"] == other
    missing = PreparedResolvedInvocation(
        "consumer", None, {"location": other, "bounds": selected}
    )
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(missing, edge, implementation)
    assert error.value.metric == "producer"
    assert error.value.path == "location"
    assert edge.diagnostic in error.value.message
