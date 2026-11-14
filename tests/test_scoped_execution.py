"""Scoped preparation exercised independently of public request integration."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.runtime.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.bindings import BoundLocation, bind_resolved
from lyra.runtime.planning.parameters import prepare_parameter_uses
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.dependencies import Dependency
from lyra.sdk.location_schema import LocationOutputSchema
from test_support.declarations import metric
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    scoped_payload,
)
from test_support.plugin_graph import graph, node
from test_support.runtime_inputs import assert_records, native_inputs
from tests.frame_assertions import assert_logical_equal
from tests.graph_arguments_helpers import Consumer, Producer, geometry, plugin
from tests.graph_execution_helpers import TABLE, executor
from tests.sqlite_helpers import database


def resolved() -> NativeLocationInput:
    return records(
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
    payload = raw_records()
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
    before = PreparedResolvedInvocation(
        "source", Producer(), native_inputs({"location": first})
    )
    after = PreparedResolvedInvocation(
        "source",
        parameters,
        native_inputs(
            {
                "location": records(
                    scoped_payload(payload), schema=output_schema(label=str)
                )
            }
        ),
    )
    assert (before.identity != after.identity) == (
        change not in {"geometry", "properties", "crs"}
    )
    assert (
        before.identity
        == PreparedResolvedInvocation(
            "source",
            Producer(),
            native_inputs(
                {"location": records(raw_records(), schema=output_schema(label=str))}
            ),
        ).identity
    )


def test_scoped_preparation_rejects_unscoped_and_missing_inputs() -> None:
    implementation = plugin(
        None, spatial=True, location_contract=node_contract("example:areas", "block")
    )
    with pytest.raises(MetricInputError, match="location"):
        implementation.prepare_resolved_invocation(
            "producer",
            native_inputs(
                {
                    "location": {
                        k: v for k, v in geometry().items() if k != "collection"
                    },
                    "bounds": resolved(),
                }
            ),
        )
    with pytest.raises(MetricInputError, match="expected fields"):
        implementation.prepare_resolved_invocation(
            "producer", native_inputs({"location": resolved()})
        )


@pytest.mark.parametrize("persistent", [False, True])
def test_bindings_retain_scope_through_chain_and_reuse(
    tmp_path: Path, *, persistent: bool
) -> None:
    received: list[tuple[gpd.GeoDataFrame, int]] = []
    free_calls: list[str] = []

    @metric(code_revision="1", name="free", description="Free", outputs={"free": TABLE})
    def free() -> pd.DataFrame:
        free_calls.append("free")
        return pd.DataFrame()

    @metric(
        code_revision="1",
        locations={"location": node_contract("example:areas", "block")},
        name="source",
        description="Source",
        outputs={"source": TABLE},
        inputs={"support": Dependency("free", producer="free")},
    )
    def source(
        location: gpd.GeoDataFrame, parameters: Producer, support: pd.DataFrame
    ) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        assert parameters.count == 2
        received.append((location, parameters.year))
        return support

    @metric(
        code_revision="1",
        locations={
            "lots": node_contract("example:areas", "block"),
            "ignored": node_contract("example:areas", "block"),
        },
        name="middle",
        description="Middle",
        outputs={"middle": TABLE},
        inputs={
            "data": Dependency("source", producer="source").bind(
                location=Input("lots")
            ),
            "other": Dependency("source", producer="source").bind(
                location=Input("ignored")
            ),
        },
    )
    def middle(
        lots: gpd.GeoDataFrame,
        ignored: gpd.GeoDataFrame,
        parameters: Consumer,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(lots, gpd.GeoDataFrame)
        assert isinstance(ignored, gpd.GeoDataFrame)
        pd.testing.assert_frame_equal(data, other)
        assert parameters.year == 2010
        return data

    @metric(
        code_revision="1",
        name="source_lot",
        description="Lot source",
        outputs={"source_lot": TABLE},
        locations={"location": node_contract("example:areas", "lot")},
        inputs={"support": Dependency("free", producer="free")},
    )
    def source_lot(
        location: gpd.GeoDataFrame, parameters: Producer, support: pd.DataFrame
    ) -> pd.DataFrame:
        return source(location, parameters, support)

    @metric(
        code_revision="1",
        name="middle_other",
        description="Mixed inputs",
        outputs={"middle_other": TABLE},
        locations={
            "lots": node_contract("example:areas", "block"),
            "ignored": node_contract("example:areas", "lot"),
        },
        inputs={
            "data": Dependency("source", producer="source").bind(
                location=Input("lots")
            ),
            "other": Dependency(
                "source_lot",
                producer="source_lot",
            ).bind(location=Input("ignored")),
        },
    )
    def middle_other(
        lots: gpd.GeoDataFrame,
        ignored: gpd.GeoDataFrame,
        parameters: Consumer,
        data: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        return middle(lots, ignored, parameters, data, other)

    @metric(
        code_revision="1",
        locations={
            "blocks": node_contract("example:areas", "block"),
            "different": node_contract("example:areas", "lot"),
        },
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            "a": Dependency(
                "middle",
                parameters={
                    "year": Input("parameters.baseline"),
                    "unrelated": Input("parameters.unrelated"),
                },
                producer="middle",
            ).bind(lots=Input("blocks"), ignored=Input("blocks")),
            "b": Dependency(
                "middle_other",
                parameters={
                    "year": Input("parameters.baseline"),
                    "unrelated": "b",
                },
                producer="middle_other",
            ).bind(lots=Input("blocks"), ignored=Input("different")),
        },
    )
    def root(
        *,
        blocks: gpd.GeoDataFrame,
        different: gpd.GeoDataFrame,
        parameters: Consumer,
        a: pd.DataFrame,
        b: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(blocks, gpd.GeoDataFrame)
        assert isinstance(different, gpd.GeoDataFrame)
        assert parameters.baseline == 2010
        assert_logical_equal(a, b)
        return a

    runner, implementation = executor(
        root, middle, source, free, middle_other, source_lot
    )
    if persistent:
        runner.store = SQLiteStore(tmp_path / "values.sqlite")
    shared = resolved()
    different = records(
        raw_records() | {"unit_type": "lot"}, schema=output_schema(label=str)
    )
    snapshot = shared.frame.copy(deep=True)
    inputs = {
        "blocks": shared,
        "different": different,
        "parameters": {},
    }
    invocation = implementation.prepare_resolved_invocation(
        "root", native_inputs(inputs)
    )
    edge = runner.graph.dependencies["root"]["a"]
    middle_input = prepare_dependency(invocation, edge, runner.plugins["plugin_1"])
    source_input = prepare_dependency(
        middle_input,
        runner.graph.dependencies["middle"]["data"],
        runner.plugins["plugin_2"],
    )
    assert set(source_input.locations) == {"location"}
    assert_records(source_input.locations["location"], shared)
    assert_records(source_input.arguments()["location"], shared)
    assert source_input.inspect_parameters() == Producer(year=2010)
    runner.execute(invocation)
    assert [year for _, year in received] == [2010, 2010]
    assert_records(received[0][0], shared)
    assert_records(received[1][0], different)
    assert free_calls == ["free"]
    pd.testing.assert_frame_equal(shared.frame, snapshot)

    # Root and middle miss cache on consumer-only data; source inputs stay equal.
    inputs["parameters"] = {"unrelated": "next job"}
    runner.execute(
        implementation.prepare_resolved_invocation("root", native_inputs(inputs))
    )
    assert len(received) == (2 if persistent else 4)
    assert len(free_calls) == (1 if persistent else 2)
    if persistent:
        assert runner.store is not None
        parameters = prepare_parameter_uses(
            runner.catalog, invocation.node, invocation.parameter_values()
        )
        bound = bind_resolved(
            runner.catalog,
            invocation.node,
            {
                name: BoundLocation(value.contract, value.ids)
                for name, value in invocation.locations.items()
            },
            parameters.uses,
            parameters.routes,
        )
        assert (
            MetricTables(SQLiteStore(tmp_path / "values.sqlite")).lookup(
                Header(
                    value_key=bound.dependencies["a"].dependencies["data"].identity,
                    node_id="source",
                    output_scope="unpartitioned",
                ),
                required=True,
            )
            is not None
        )


def test_automatic_scoped_forwarding_and_producer_defaults() -> None:
    @metric(
        code_revision="1",
        locations={"location": node_contract("example:areas", "block")},
        name="source",
        description="Source",
        outputs={"source": TABLE},
    )
    def source(location: gpd.GeoDataFrame, parameters: Producer) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        assert parameters == Producer()
        return pd.DataFrame()

    @metric(
        code_revision="1",
        locations={"location": node_contract("example:areas", "block")},
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={"data": Dependency("source", producer="source")},
    )
    def root(location: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        return data

    runner, implementation = executor(root, source)
    location = resolved()
    invocation = implementation.prepare_resolved_invocation(
        "root", native_inputs({"location": location})
    )
    prepared = prepare_dependency(
        invocation,
        runner.graph.dependencies["root"]["data"],
        runner.plugins["plugin_1"],
    )
    assert_records(prepared.locations["location"], location)
    assert prepared.inspect_parameters() == Producer()
    runner.execute(invocation)


def test_binding_precedes_matching_name_and_aliases_do_not_change_identity() -> None:
    registry = graph(
        node(
            "consumer",
            spatial={"location", "bounds", "alias"},
            inputs={
                "data": Dependency(
                    "producer",
                    producer="producer",
                ).bind(location=Input("alias"))
            },
        ),
        node("producer", spatial={"location", "bounds"}),
    )
    selected = resolved()
    other = records(
        raw_records() | {"collection": "other:areas"},
        schema=output_schema(label=str),
    )
    consumer = PreparedResolvedInvocation(
        "consumer",
        None,
        native_inputs({"location": other, "bounds": selected, "alias": selected}),
    )
    implementation = plugin(
        None, spatial=True, location_contract=node_contract("example:areas", "block")
    )
    edge = registry.dependencies["consumer"]["data"]
    prepared = prepare_dependency(consumer, edge, implementation)
    direct = implementation.prepare_resolved_invocation(
        "producer", native_inputs({"location": selected, "bounds": selected})
    )
    assert prepared.identity == direct.identity
    assert_records(prepared.locations["location"], selected)
    assert_records(consumer.locations["location"], other)
    missing = PreparedResolvedInvocation(
        "consumer", None, native_inputs({"location": other, "bounds": selected})
    )
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(missing, edge, implementation)
    assert error.value.metric == "producer"
    assert error.value.path == "location"
    assert edge.diagnostic in error.value.message


def raw_records() -> dict:
    return scoped_payload(geometry()) | {
        "collection": "example:areas",
        "unit_type": "block",
    }


def records(payload: dict, *, schema: LocationOutputSchema) -> NativeLocationInput:
    frame = fixture_locations(payload, schema=schema)
    return NativeLocationInput(
        frame,
        node_contract(payload["collection"], payload["unit_type"]).reference,
        "id",
    )


__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")
