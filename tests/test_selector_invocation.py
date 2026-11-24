"""Callbacks consume independent working inputs without acquiring candidates."""

from dataclasses import replace

import geopandas as gpd
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest
from geopandas.testing import assert_geodataframe_equal
from pydantic import Field
from shapely.geometry import Point

from lyra.contracts.dependencies import Input, LocationDependency
from lyra.runtime.arguments import prepare_dependency
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_operations import Places
from test_support.table_fixtures import value_frame, value_schema
from tests.provider_schema_helpers import frame
from tests.selector_helpers import prepared


class Parameters(MetricParameters):
    groups: dict[str, list[int]] = Field(
        default_factory=lambda: {"limits": [2]}, description="Candidate limits"
    )


@pytest.mark.parametrize("values", [{}, {"groups": {"limits": [7, 8]}}])
def test_positional_callbacks_own_frames_parameters_and_identity(values: dict) -> None:
    expected = Parameters.model_validate(values).groups
    seen: list[str] = []

    def selector(
        unrelated: gpd.GeoDataFrame, symbolic: ir.Table, /, *, parameters: Parameters
    ) -> ir.Table:
        seen.append("selector")
        assert isinstance(symbolic.op(), ops.UnboundTable)
        assert symbolic.columns == ("location_id", "geometry", "count")
        assert parameters.groups == expected
        unrelated.loc[0, "location_id"] = "forged"
        unrelated["geometry"] = gpd.GeoSeries(
            [Point(9, 9)] * len(unrelated), crs=unrelated.crs
        )
        unrelated.attrs["nested"]["values"].append(9)
        unrelated.set_crs(3857, allow_override=True, inplace=True)
        parameters.groups["limits"].append(99)
        return symbolic.filter(symbolic["count"] > expected["limits"][0])

    def mapping(
        records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame, /, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        seen.append("mapping")
        assert_geodataframe_equal(records, original)
        assert records.attrs == original.attrs
        assert parameters.groups == expected
        pool.loc[0, "location_id"] = "changed"
        pool["geometry"] = gpd.GeoSeries([Point(8, 8)] * len(pool), crs=pool.crs)
        pool.attrs["nested"]["values"].append(8)
        pool.set_crs(3857, allow_override=True, inplace=True)
        parameters.groups["limits"].clear()
        return {"a": ("a",), "b": ("a",)}

    binding, invocation, target, candidates = prepared(mapping, selector, values)
    original = frame()
    original.attrs = {"nested": {"values": [1]}}
    invocation = replace(
        invocation,
        parameters=invocation.inspect_parameters(),
        locations={
            "declared_source": replace(
                invocation.locations["declared_source"], frame=original
            )
        },
    )
    candidates = replace(candidates, frame=original)
    identity = invocation.identity
    assert binding.invoke_target_selector(invocation, target) is not None
    assert binding.invoke_operation(invocation, candidates) == {
        "a": ("a",),
        "b": ("a",),
    }
    assert seen == ["selector", "mapping"]
    assert invocation.identity == identity
    assert invocation.parameter_values() == {"groups": expected}
    assert_geodataframe_equal(invocation.locations["declared_source"].frame, original)
    assert_geodataframe_equal(candidates.frame, original)
    assert candidates.frame.attrs == original.attrs
    assert candidates.ids == ("a", "b")
    assert candidates.contract == target.contract.reference


@pytest.mark.parametrize("empty_source", [False, True])
@pytest.mark.parametrize("no_match_error", [False, True])
def test_empty_inputs_preserve_callback_semantics(
    *, empty_source: bool, no_match_error: bool
) -> None:
    events: list[str] = []

    def selector(unrelated: gpd.GeoDataFrame, symbolic: ir.Table, /) -> ir.Table:
        assert len(unrelated) == 2
        events.append("selector")
        return symbolic

    def mapping(records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame, /) -> dict:
        assert len(records) == 2
        events.append("mapping")
        assert_geodataframe_equal(pool, frame().iloc[:0])
        if no_match_error:
            message = "Domain requires a match"
            raise RuntimeError(message)
        return {}

    binding, invocation, target, candidates = prepared(mapping, selector)
    candidates = replace(candidates, frame=candidates.frame.iloc[:0])
    if empty_source:
        source = invocation.locations["declared_source"]
        invocation = replace(
            invocation,
            parameters=None,
            locations={"declared_source": replace(source, frame=source.frame.iloc[:0])},
        )
        assert binding.invoke_target_selector(invocation, target) is None
        assert binding.invoke_operation(invocation) == {}
        assert events == []
    else:
        assert binding.invoke_target_selector(invocation, target) is not None
        if no_match_error:
            with pytest.raises(RuntimeError, match="Domain requires"):
                binding.invoke_operation(invocation, candidates)
        else:
            assert binding.invoke_operation(invocation, candidates) == {
                "a": (),
                "b": (),
            }
        assert events == ["selector", "mapping"]


@pytest.mark.parametrize("failure", ["author", "shape", "predicate"])
def test_selector_failures_stop_before_mapping(failure: str) -> None:
    def selector(unrelated: gpd.GeoDataFrame, symbolic: ir.Table) -> object:
        del unrelated
        if failure == "author":
            message = "Selector failed"
            raise RuntimeError(message)
        if failure == "shape":
            return symbolic.limit(1)
        return symbolic["count"] > 0

    def mapping(records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> dict:
        pytest.fail(f"Mapping reached after invalid selection: {records}, {pool}")

    binding, invocation, target, candidates = prepared(mapping, selector)
    exception = {"author": RuntimeError, "shape": ValueError, "predicate": TypeError}[
        failure
    ]

    def execute() -> None:
        binding.invoke_target_selector(invocation, target)
        binding.invoke_operation(invocation, candidates)

    with pytest.raises(exception):
        execute()


@pytest.mark.parametrize(
    "result", [{"outside": ()}, {"a": ("a", "a")}, {"a": ["a"]}, {"a": (1,)}, []]
)
def test_mapping_validation_uses_original_source(result: object) -> None:
    def selector(unrelated: gpd.GeoDataFrame, symbolic: ir.Table) -> ir.Table:
        del unrelated
        return symbolic

    def mapping(records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> object:
        del pool
        records.loc[0, "location_id"] = "outside"
        return result

    binding, invocation, _, candidates = prepared(mapping, selector)
    with pytest.raises(ValueError, match=r"Operation|validation error"):
        binding.invoke_operation(invocation, candidates)


def test_incoming_correspondence_rows_survive_both_callbacks() -> None:
    observed: list[list[tuple[str, str]]] = []

    def record(source: gpd.GeoDataFrame) -> None:
        observed.append(
            list(zip(source.source_location_id, source.location_id, strict=True))
        )

    def selector(unrelated: gpd.GeoDataFrame, symbolic: ir.Table) -> ir.Table:
        record(unrelated)
        return symbolic

    def mapping(records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> dict:
        del pool
        record(records)
        return {"a": ("b",)}

    binding, invocation, target, candidates = prepared(mapping, selector)
    source = invocation.locations["declared_source"]
    pairs = source.frame.iloc[[0, 0]].copy()
    pairs.insert(0, "source_location_id", ["upstream-a", "upstream-b"])
    invocation = replace(
        invocation,
        parameters=None,
        locations={
            "declared_source": replace(
                source, frame=pairs, source_scope=source.contract
            )
        },
    )
    binding.invoke_target_selector(invocation, target)
    assert binding.invoke_operation(invocation, candidates) == {"a": ("b",)}
    assert observed == [[("upstream-a", "a"), ("upstream-b", "a")]] * 2


def test_explicit_forwarding_supplies_both_callbacks() -> None:
    seen: list[dict[str, list[int]]] = []

    def selector(
        source: gpd.GeoDataFrame, relation: ir.Table, *, parameters: Parameters
    ) -> ir.Table:
        del source
        seen.append(parameters.groups)
        return relation

    def mapping(
        source: gpd.GeoDataFrame,
        candidates: gpd.GeoDataFrame,
        *,
        parameters: Parameters,
    ) -> dict:
        del source, candidates
        seen.append(parameters.groups)
        return {}

    binding, invocation, target, candidates = prepared(mapping, selector, {})

    @metric(
        name="consumer",
        code_revision="1",
        description="Consume selected records",
        inputs={
            "records": LocationDependency(
                "select_targets", parameters={"groups": Input("parameters.groups")}
            ).bind(declared_source=Input("seed")),
            "seed": LocationDependency(provider=Places.id),
        },
        outputs={"result": value_schema()},
    )
    def consumer(
        records: gpd.GeoDataFrame, seed: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> pd.DataFrame:
        del records, seed, parameters
        return value_frame()

    definition = PluginDefinition(metrics=[consumer])
    definitions = bind_native_definitions(
        {"operations": binding.definition, "consumer": definition}
    )
    catalog = compile_live_catalog(definitions)
    owner = BoundPlugin(definitions["consumer"]).prepare_resolved_invocation(
        "consumer",
        {
            "seed": invocation.locations["declared_source"],
            "parameters": {"groups": {"limits": [11]}},
        },
    )
    edge = catalog.graph.location_dependencies["consumer"]["inputs.records"]
    forwarded = prepare_dependency(owner, edge, binding)
    binding.invoke_target_selector(forwarded, target)
    assert binding.invoke_operation(forwarded, candidates) == {"a": (), "b": ()}
    assert seen == [{"limits": [11]}, {"limits": [11]}]
    assert seen[0] is not seen[1]
