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
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.plugin import metric
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    scoped_payload,
)
from test_support.routing_plugins import provider
from test_support.runtime_inputs import assert_records
from test_support.table_fixtures import value_frame
from tests.frame_assertions import assert_logical_equal
from tests.graph_arguments_helpers import (
    Consumer,
    Producer,
    geometry,
    location_edge,
    plugin,
    scoped_frame,
    scoped_inputs,
)
from tests.graph_execution_helpers import TABLE, executor
from tests.sqlite_helpers import database


def resolved() -> NativeLocationInput:
    return records(
        scoped_payload(geometry()) | {"provider": "example:areas_block"},
        schema=output_schema(label=str),
    )


@pytest.mark.parametrize(
    "change",
    ["provider", "id", "geometry", "properties", "crs", "parameters"],
)
def test_scoped_identity_includes_all_effective_inputs(change: str) -> None:
    first = resolved()
    payload = raw_records()
    parameters = Producer()
    match change:
        case "provider":
            payload["provider"] = "other:areas"
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
        "source", Producer(), scoped_inputs({"location": first})
    )
    after = PreparedResolvedInvocation(
        "source",
        parameters,
        scoped_inputs(
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
            scoped_inputs(
                {"location": records(raw_records(), schema=output_schema(label=str))}
            ),
        ).identity
    )


def test_scoped_preparation_rejects_unscoped_and_missing_inputs() -> None:
    implementation = plugin(
        None, spatial=True, location_contract=node_contract("example:areas_block")
    )
    with pytest.raises(MetricInputError, match="location"):
        implementation.prepare_resolved_invocation(
            "producer",
            scoped_inputs(
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
            "producer", scoped_inputs({"location": resolved()})
        )


@pytest.mark.parametrize("persistent", [False, True])
def test_bindings_retain_scope_through_chain_and_reuse(
    tmp_path: Path, *, persistent: bool
) -> None:
    received: list[tuple[gpd.GeoDataFrame, int]] = []
    free_calls: list[str] = []

    @metric(
        cache="full",
        code_revision="1",
        name="free",
        description="Free",
        outputs={"free": TABLE},
    )
    def free() -> pd.DataFrame:
        free_calls.append("free")
        return value_frame()

    @metric(
        cache="full",
        code_revision="1",
        inputs={
            "location": LocationDependency(provider="example:areas_block"),
            "support": Dependency("free", producer="free"),
        },
        name="source",
        description="Source",
        outputs={"source": TABLE},
    )
    def source(
        location: gpd.GeoDataFrame, parameters: Producer, support: pd.DataFrame
    ) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        assert parameters.count == 2
        received.append((location, parameters.year))
        return support

    @metric(
        cache="full",
        code_revision="1",
        inputs={
            "lots": LocationDependency(provider="example:areas_block"),
            "ignored": LocationDependency(provider="example:areas_block"),
            "data": Dependency("source", producer="source").bind(
                location=Input("lots")
            ),
            "other": Dependency("source", producer="source").bind(
                location=Input("ignored")
            ),
        },
        name="middle",
        description="Middle",
        outputs={"middle": TABLE},
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
        cache="full",
        code_revision="1",
        name="source_lot",
        description="Lot source",
        outputs={"source_lot": TABLE},
        inputs={
            "location": LocationDependency(provider="example:areas_lot"),
            "support": Dependency("free", producer="free"),
        },
    )
    def source_lot(
        location: gpd.GeoDataFrame, parameters: Producer, support: pd.DataFrame
    ) -> pd.DataFrame:
        return source(location, parameters, support)

    @metric(
        cache="full",
        code_revision="1",
        name="middle_other",
        description="Mixed inputs",
        outputs={"middle_other": TABLE},
        inputs={
            "lots": LocationDependency(provider="example:areas_block"),
            "ignored": LocationDependency(provider="example:areas_lot"),
            "data": Dependency("source", producer="source").bind(
                location=Input("lots")
            ),
            "other": Dependency("source_lot", producer="source_lot").bind(
                location=Input("ignored")
            ),
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
        cache="full",
        code_revision="1",
        inputs={
            "blocks": LocationDependency(provider="example:areas_block"),
            "different": LocationDependency(provider="example:areas_lot"),
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
                parameters={"year": Input("parameters.baseline"), "unrelated": "b"},
                producer="middle_other",
            ).bind(lots=Input("blocks"), ignored=Input("different")),
        },
        name="root",
        description="Root",
        outputs={"root": TABLE},
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
        root,
        middle,
        source,
        free,
        middle_other,
        source_lot,
        resources={
            key: provider(key) for key in ("example:areas_block", "example:areas_lot")
        },
    )
    if persistent:
        runner.store = SQLiteStore(tmp_path / "values.sqlite")
    shared = resolved()
    different = records(
        raw_records() | {"provider": "example:areas_lot"},
        schema=output_schema(label=str),
    )
    snapshot = shared.frame.copy(deep=True)
    inputs = {
        "blocks": shared,
        "different": different,
        "parameters": {},
    }
    invocation = implementation.prepare_resolved_invocation(
        "root", scoped_inputs(inputs)
    )
    edge = runner.graph.dependencies["root"]["a"]
    middle_input = prepare_dependency(
        invocation, edge, runner.catalog.implementations["plugin_1"]
    )
    source_input = prepare_dependency(
        middle_input,
        runner.graph.dependencies["middle"]["data"],
        runner.catalog.implementations["plugin_2"],
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
        implementation.prepare_resolved_invocation("root", scoped_inputs(inputs))
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
        cache="full",
        code_revision="1",
        inputs={
            "location": LocationDependency(provider="example:areas_block"),
        },
        name="source",
        description="Source",
        outputs={"source": TABLE},
    )
    def source(location: gpd.GeoDataFrame, parameters: Producer) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        assert parameters == Producer()
        return value_frame()

    @metric(
        cache="full",
        code_revision="1",
        inputs={
            "location": LocationDependency(provider="example:areas_block"),
            "data": Dependency("source", producer="source"),
        },
        name="root",
        description="Root",
        outputs={"root": TABLE},
    )
    def root(location: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert isinstance(location, gpd.GeoDataFrame)
        return data

    runner, implementation = executor(
        root, source, resources={"example:areas_block": provider("example:areas_block")}
    )
    location = resolved()
    invocation = implementation.prepare_resolved_invocation(
        "root", scoped_inputs({"location": location})
    )
    prepared = prepare_dependency(
        invocation,
        runner.graph.dependencies["root"]["data"],
        runner.catalog.implementations["plugin_1"],
    )
    assert_records(prepared.locations["location"], location)
    assert prepared.inspect_parameters() == Producer()
    runner.execute(invocation)


def test_binding_precedes_matching_name_and_aliases_do_not_change_identity() -> None:
    selected = resolved()
    other = records(
        raw_records() | {"provider": "other:areas"},
        schema=output_schema(label=str),
    )
    consumer = PreparedResolvedInvocation(
        "consumer",
        None,
        scoped_inputs({"location": other, "bounds": selected, "alias": selected}),
    )
    implementation = plugin(
        None, spatial=True, location_contract=node_contract("example:areas_block")
    )
    edge = location_edge(alias=True, contract=node_contract("example:areas_block"))
    prepared = prepare_dependency(consumer, edge, implementation)
    direct = implementation.prepare_resolved_invocation(
        "producer", scoped_inputs({"location": selected, "bounds": selected})
    )
    assert prepared.identity == direct.identity
    assert_records(prepared.locations["location"], selected)
    assert_records(consumer.locations["location"], other)
    missing = PreparedResolvedInvocation(
        "consumer", None, scoped_inputs({"location": other, "bounds": selected})
    )
    with pytest.raises(MetricInputError, match="missing spatial input") as error:
        prepare_dependency(missing, edge, implementation)
    assert error.value.metric == "producer"
    assert error.value.path == "location"
    assert edge.diagnostic in error.value.message


def raw_records() -> dict:
    return scoped_payload(geometry()) | {
        "provider": "example:areas_block",
    }


def records(payload: dict, *, schema: LocationOutputSchema) -> NativeLocationInput:
    frame = fixture_locations(payload, schema=schema)
    return scoped_frame(frame, node_contract(payload["provider"]))


__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")
