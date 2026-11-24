"""Correspondence tables preserve provider values and immediate pair identities."""

import gc
import traceback
import weakref
from collections.abc import Mapping
from dataclasses import replace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.locations import LocationSelection
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput, inputs_equal
from lyra.runtime.operations import acquire_operation_targets, forward_correspondences
from lyra.runtime.planning.bindings import BoundLocation
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.resources import ResourceScope
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import Places
from test_support.location_schema import column
from tests.provider_execution_helpers import RECORDS, RESULT


class Targets(Places):
    id = "targets"
    schema = RECORDS.update_column("value", dtype="Int64", nullable=True)
    # This provider-wide cardinality requirement cannot apply after expansion.
    schema = pa.DataFrameSchema(
        schema.columns,
        strict=True,
        checks=pa.Check(lambda frame: len(frame) <= 2, name="at_most_two"),
    )

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {
                "location_id": list(reversed(ids)),
                "value": pd.array([None] * len(ids), dtype="Int64"),
            },
            geometry=[Point(4, 5)] * len(ids),
            crs=4326,
        )


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_expansion_preserves_schema_scopes_and_completed_groups(
    *, empty: bool, reverse: bool
) -> None:
    runner = LocalRunner.from_definitions(
        {"test": PluginDefinition(resources={"source": Places, "target": Targets})}
    )
    registry = runner.catalog.provider_registry
    scope = ResourceScope(runner.catalog.implementations["test"].resources)
    with scope.activate():
        resolver = LocationResolver(scope, providers=registry)
        source = resolver.resolve(
            {
                "source": LocationSelection(
                    provider=Places.id,
                    ids=["C", "B", "A"] if reverse else ["A", "B", "C"],
                )
            }
        )["source"]
        groups = {
            key: ()
            if empty
            else {"A": ("Y", "X") if reverse else ("X", "Y"), "B": ("Y",), "C": ()}[key]
            for key in source.ids
        }
        result = acquire_operation_targets(groups, source, Targets.id, resolver)
        forwarded = forward_correspondences(
            result, registry[Targets.id].schema.metadata
        )
    assert result.groups["C"] == ()
    assert forwarded.source_scope == source.contract
    assert forwarded.contract == result.targets.contract
    assert forwarded.description is not None
    assert forwarded.description.unique == ["source_location_id", "location_id"]
    attribute = next(
        column for column in forwarded.description.columns if column.name == "value"
    )
    original = next(
        column
        for column in registry[Targets.id].schema.metadata.columns
        if column.name == "value"
    )
    assert attribute == original
    frame = forwarded.frame
    assert frame.geometry.name == "geometry"
    assert frame.crs == result.targets.frame.crs
    assert str(frame.value.dtype) == "Int64"
    assert frame.value.isna().all()
    pairs = set(zip(frame.source_location_id, frame.location_id, strict=True))
    assert pairs == (set() if empty else {("A", "X"), ("A", "Y"), ("B", "Y")})
    if not empty:
        shared = frame[frame.location_id == "Y"].drop(columns="source_location_id")
        pd.testing.assert_series_equal(
            shared.iloc[0], shared.iloc[1], check_names=False
        )
    permuted = NativeLocationInput(
        frame.iloc[::-1],
        forwarded.contract,
        source_scope=forwarded.source_scope,
        description=forwarded.description,
    )
    assert inputs_equal(permuted, forwarded)
    invocation = PreparedResolvedInvocation("consumer", None, {"places": forwarded})
    assert invocation.locations["places"].source_scope == source.contract
    assert invocation.locations["places"].description == forwarded.description
    assert set(invocation.locations["places"].ids) == (set() if empty else {"X", "Y"})


def chain_runner(
    seen: list[gpd.GeoDataFrame], *, duplicate: bool = False, empty: bool = False
) -> LocalRunner:
    @location_operation(
        code_revision="1",
        name="first",
        description="Select targets",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Targets.id,
        canonical=True,
    )
    def first(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        assert set(source.location_id) == {"A", "B", "C"}
        return {} if empty else {"A": ("X", "Y"), "B": ("Y",)}

    @location_operation(
        code_revision="1",
        name="second",
        description="Select same-provider targets",
        inputs={"middle": LocationDependency(provider=Targets.id)},
        target_provider=Targets.id,
    )
    def second(middle: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        seen.append(middle.copy())
        assert set(zip(middle.source_location_id, middle.location_id, strict=True)) == {
            ("A", "X"),
            ("A", "Y"),
            ("B", "Y"),
        }
        return {"X": ("Y",), "Y": ("X",)}

    @metric(
        code_revision="1",
        name="owned",
        description="Own results by targets",
        inputs={"places": LocationDependency(provider=Targets.id, primary=True)},
        outputs={
            "data": pa.DataFrameSchema(
                {
                    "location_id": column(str),
                    "row_key": column(str),
                    "value": column("Int64", nullable=True),
                },
                strict=True,
                unique=["location_id", "row_key"],
            )
        },
    )
    def owned(places: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(places.copy())
        return pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": "same" if duplicate else places.source_location_id,
                "value": pd.array([None] * len(places), dtype="Int64"),
            }
        )

    @metric(
        code_revision="1",
        name="chain",
        description="Nested operation input",
        inputs={
            "source": LocationDependency(provider=Places.id),
            "final": LocationDependency(operation="second").bind(
                middle=LocationDependency(operation="first").bind(
                    source=Input("source")
                )
            ),
        },
        outputs={"data": RESULT},
    )
    def chain(source: gpd.GeoDataFrame, final: gpd.GeoDataFrame) -> pd.DataFrame:
        assert set(source.location_id) == {"A", "B", "C"}
        seen.append(final.copy())
        assert set(zip(final.source_location_id, final.location_id, strict=True)) == (
            set() if empty else {("X", "Y"), ("Y", "X")}
        )
        assert str(final.value.dtype) == "Int64"
        assert final.crs is not None
        assert final.crs.to_epsg() == 4326
        return pd.DataFrame({"total": [len(final)]})

    @metric(
        code_revision="1",
        name="dependent",
        description="Route into target-owned dependency rows",
        inputs={
            "source": LocationDependency(provider=Places.id),
            "data": Dependency("data", producer="owned").bind(places=Input("source")),
        },
        outputs={"data": RESULT},
    )
    def dependent(source: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert set(source.location_id) == {"A", "B", "C"}
        assert str(data.value.dtype) == "Int64"
        assert data.value.isna().all()
        return pd.DataFrame({"total": [len(data)]})

    return LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[owned, chain, dependent],
                location_operations=[first, second],
                resources={"source": Places, "target": Targets},
            )
        }
    )


@pytest.mark.parametrize("empty", [False, True])
def test_receiving_primary_preserves_nullable_pairs_and_empty_completion(
    *, empty: bool
) -> None:
    seen: list[gpd.GeoDataFrame] = []
    runner = chain_runner(seen, empty=empty)
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "dependent",
            "inputs": {"source": {"provider": Places.id, "ids": ["A", "B", "C"]}},
        },
    )
    outcome = GraphExecutor(runner.catalog, Mock()).execute(plan)
    assert outcome.outputs["data"].total.tolist() == [0 if empty else 3]
    completion = next(item for item in outcome.completions if item.scope == "location")
    assert (
        completion.provider_scope
        == runner.catalog.provider_registry[Targets.id].contract.reference
    )
    assert completion.partitions == (set() if empty else {"X", "Y"})
    if not empty:
        frame = seen[0]
        assert str(frame.value.dtype) == "Int64"
        assert frame.value.isna().all()
        assert frame.crs == "EPSG:4326"
        assert set(zip(frame.source_location_id, frame.location_id, strict=True)) == {
            ("A", "X"),
            ("A", "Y"),
            ("B", "Y"),
        }


@pytest.mark.parametrize("empty", [False, True])
def test_nested_chain_retains_immediate_pairs_and_same_provider_roles(
    *, empty: bool
) -> None:
    seen: list[gpd.GeoDataFrame] = []
    runner = chain_runner(seen, empty=empty)
    assert runner.run(
        "chain", inputs={"source": {"provider": Places.id, "ids": ["A", "B", "C"]}}
    ).tables["data"].total.tolist() == [0 if empty else 2]
    assert len(seen) == (1 if empty else 2)


@pytest.mark.parametrize("duplicate", [False, True])
def test_routed_primary_owns_targets_and_preserves_incoming_pairs(
    *, duplicate: bool
) -> None:
    seen: list[gpd.GeoDataFrame] = []
    runner = chain_runner(seen, duplicate=duplicate)
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "owned",
            "inputs": {"places": {"provider": Places.id, "ids": ["C", "B", "A"]}},
        },
    )
    executor = GraphExecutor(runner.catalog, Mock())
    if duplicate:
        with pytest.raises(GraphExecutionError, match=r"unique|duplicate"):
            executor.execute(plan)
    else:
        outcome = executor.execute(plan)
        assert len(outcome.outputs["data"]) == 3
        assert outcome.completions[0].partitions == frozenset({"X", "Y"})
        assert (
            outcome.completions[0].provider_scope
            == runner.catalog.provider_registry[Targets.id].contract.reference
        )
    assert len(seen[0]) == 3


def test_automatic_route_reduced_target_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[gpd.GeoDataFrame] = []
    runner = chain_runner(seen)
    prepare = BoundPlugin.prepare_resolved_invocation

    def reduced(
        plugin: BoundPlugin, name: str, inputs: Mapping[str, object]
    ) -> PreparedResolvedInvocation:
        invocation = prepare(plugin, name, inputs)
        if name == "owned":
            return invocation.select_primary("places", frozenset({"Y"}))
        return invocation

    monkeypatch.setattr(BoundPlugin, "prepare_resolved_invocation", reduced)
    result = runner.run(
        "owned", inputs={"places": {"provider": Places.id, "ids": ["A", "B", "C"]}}
    ).tables
    assert set(
        zip(result["data"].row_key, result["data"].location_id, strict=True)
    ) == {("A", "Y"), ("B", "Y")}
    assert set(seen[0].location_id) == {"Y"}


@pytest.mark.parametrize("empty", [False, True])
def test_operation_derived_owner_is_target(*, empty: bool) -> None:
    runner = chain_runner([], empty=empty)
    result = runner.run(
        "owned", inputs={"places": {"provider": Places.id, "ids": ["A", "B", "C"]}}
    )
    description = result.metadata.outputs["data"]
    assert description.scope == "location"
    assert (
        description.owner_scope
        == runner.catalog.provider_registry[
            Targets.id
        ].contract.reference.provider_scope
    )
    assert description.owner_scope is not None
    assert description.owner_scope.provider_id == Targets.id
    assert result.tables["data"].empty is empty


@pytest.mark.parametrize("reverse", [False, True])
def test_bound_pair_snapshots_preserve_order_and_shared_targets(
    *, reverse: bool
) -> None:
    runner = chain_runner([])
    provider = runner.catalog.provider_registry[Targets.id].contract.reference
    source = BoundLocation(provider, ("A", "B", "C"))
    pairs = (("B", "Y"), ("A", "X"), ("A", "Y"), ("B", "Y"))
    if reverse:
        pairs = pairs[::-1]
    target = BoundLocation(
        provider,
        ("X", "Y"),
        pairs=pairs,
        source=source,
        computations=(("all", None), ("a", "A"), ("b", "B"), ("c", "C")),
    )
    identity = target.identity
    shared = target.select(frozenset({"Y"}))
    assert shared.pairs == tuple(pair for pair in pairs if pair[1] == "Y")
    assert shared.computations == (("all", None), ("a", "A"), ("b", "B"))
    represented = target.select(frozenset({"Y", "X"}))
    assert represented.pairs == pairs
    assert represented.computations == (("all", None), ("a", "A"), ("b", "B"))
    assert shared.select(frozenset({"X"})).pairs == ()
    assert target.identity == identity
    assert replace(target) == target
    for ids in (frozenset({"A"}), frozenset({"A", "B"}), frozenset({"C"})):
        selected = target.related(source.select(ids))
        assert selected is not None
        assert selected.pairs == tuple(pair for pair in pairs if pair[0] in ids)
        assert selected.computations == tuple(
            (key, group)
            for key, group in target.computations
            if group is None or group in ids
        )
    replacement = replace(target, pairs=(("C", "Y"),))
    assert replacement.select(frozenset({"Y"})).pairs == (("C", "Y"),)
    restricted = replacement.related(source.select(frozenset({"A"})))
    assert restricted is not None
    assert restricted.pairs == ()
    assert target.related(BoundLocation(provider, ("A",))) is None


def test_bound_pair_multihop_and_sibling_selections() -> None:
    runner = chain_runner([])
    provider = runner.catalog.provider_registry[Targets.id].contract.reference
    source = BoundLocation(provider, ("A", "B", "C"))
    middle = BoundLocation(
        provider,
        ("X", "Y"),
        source=source,
        pairs=(("A", "X"), ("B", "Y"), ("A", "Y")),
    )
    final = BoundLocation(
        provider,
        ("P", "Q"),
        source=middle,
        pairs=(("Y", "Q"), ("X", "P"), ("Y", "P")),
    )
    sibling = BoundLocation(
        provider,
        ("S", "T"),
        source=source,
        pairs=(("C", "T"), ("B", "S"), ("A", "S")),
        computations=(("full", None), ("b", "B"), ("a", "A"), ("c", "C")),
    )
    selected = final.related(source.select(frozenset({"B"})))
    assert selected is not None
    assert selected.pairs == (("Y", "Q"), ("Y", "P"))
    ancestor = source.related(selected)
    branch = sibling.related(selected)
    empty = final.related(source.select(frozenset({"C"})))
    assert ancestor is not None
    assert ancestor.ids == ("A", "B")
    assert branch is not None
    assert branch.pairs == (("B", "S"), ("A", "S"))
    assert empty is not None
    assert empty.ids == ()

    def native(bound: BoundLocation) -> NativeLocationInput:
        ids = [b for _, b in bound.pairs] if bound.source else list(bound.ids or ())
        frame = gpd.GeoDataFrame(
            {"location_id": ids}, geometry=[Point(0, 1)] * len(ids), crs=4326
        )
        if bound.source:
            frame["source_location_id"] = [a for a, _ in bound.pairs]
        frame.index = [1] * len(frame)
        return NativeLocationInput(
            frame,
            provider,
            source_scope=provider if bound.source else None,
            source=bound.source.membership if bound.source else None,
            lineage=bound.lineage,
            computations=bound.computations,
        )

    native_source = native(source)
    native_final = native(final)
    selected_native = native_final.related_selection(
        native_source.select_ids(frozenset({"B"}))
    )
    assert selected_native is not None
    for actual, expected in [
        (selected_native, selected),
        (native(sibling).related_selection(selected_native), branch),
        (native_source.related_selection(selected_native), ancestor),
        (
            native_final.related_selection(native_source.select_ids(frozenset({"C"}))),
            empty,
        ),
    ]:
        assert actual is not None
        assert set(actual.ids) == set(expected.ids or ())
        if actual.source_scope is not None:
            assert (
                tuple(
                    zip(
                        actual.frame.source_location_id,
                        actual.frame.location_id,
                        strict=True,
                    )
                )
                == expected.pairs
            )
        assert actual.computations == expected.computations
    assert (
        native_final.related_selection(native(BoundLocation(provider, ("B",)))) is None
    )


def test_unknown_bound_membership_stays_symbolic() -> None:
    provider = chain_runner([]).catalog.provider_registry[Targets.id].contract.reference
    source = BoundLocation(provider, ("A", "B"))
    unresolved = BoundLocation(provider, None, source=source)
    symbolic = unresolved.related(source.select(frozenset({"B"})))
    assert symbolic is not None
    assert symbolic.ids is None
    back = source.related(unresolved)
    assert back is not None
    assert back.ids is None
    unknown = BoundLocation(provider, None)
    assert unknown.select(frozenset()).ids is None
    assert BoundLocation(provider, ()).select(frozenset()).ids == ()


def test_pair_index_lifetime_follows_snapshot_after_failure() -> None:
    """Indexes must not retain a graph after its owner releases it on failure."""
    runner = chain_runner([])
    provider = runner.catalog.provider_registry[Targets.id].contract.reference
    references = []

    def traverse() -> None:
        location = BoundLocation(provider, ("X", "Y"), pairs=(("A", "X"),))
        location.select(frozenset({"X"}))
        references.append(weakref.ref(location.membership.pair_index))
        message = "failed traversal"
        raise ValueError(message)

    try:
        traverse()
    except ValueError as error:
        traceback.clear_frames(error.__traceback__)
    gc.collect()
    assert all(reference() is None for reference in references)
