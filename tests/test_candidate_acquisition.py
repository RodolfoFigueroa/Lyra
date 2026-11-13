"""Selected records remain authoritative across mapping, forwarding, and reuse."""

from dataclasses import replace
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import Point

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.resources import ResourceConfig
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.operations import execute_operation, forward_correspondences
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.resources import ResourceScope
from lyra.sdk import Input, LocationDependency, PluginDefinition, metric
from lyra.sdk.provider_schema import ProviderSchema
from test_support.location_operations import Places
from test_support.location_schema import column
from tests.provider_schema_helpers import frame
from tests.selector_helpers import ManagedProvider, plugin, prepared


def resources(
    monkeypatch: pytest.MonkeyPatch,
    target: RegisteredProvider,
    output: gpd.GeoDataFrame,
    events: list[str],
    *,
    failure: str | None = None,
) -> tuple[ResourceScope, LocationResolver, Mock]:
    def setup(_self: ManagedProvider) -> None:
        events.append("setup")
        if failure == "setup":
            message = "setup failed"
            raise RuntimeError(message)

    def select(
        _self: ManagedProvider, expression: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        events.append("query")
        assert isinstance(expression, ir.Table)
        assert schema is target.schema
        if failure in {"query", "decode"}:
            message = f"{failure} failed"
            raise RuntimeError(message)
        return output

    monkeypatch.setattr(ManagedProvider, "secret_names", ())
    monkeypatch.setattr(ManagedProvider, "setup", setup)
    monkeypatch.setattr(ManagedProvider, "close", lambda _self: events.append("close"))
    monkeypatch.setattr(ManagedProvider, "select", select)
    exact = Mock(side_effect=AssertionError("Unexpected exact-ID acquisition"))
    monkeypatch.setattr(
        ManagedProvider,
        "resolve",
        create_autospec(ManagedProvider.resolve, side_effect=exact),
    )
    scope = ResourceScope(
        {"managed": ManagedProvider},
        {"managed": ResourceConfig(config={"label": "fake"})},
    )
    return scope, LocationResolver(scope, providers={"managed": target}), exact


def select_all(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
    del source
    return relation


@pytest.mark.parametrize("repeated", [False, True])
def test_authoritative_forwarding_and_query_membership(
    monkeypatch: pytest.MonkeyPatch, *, repeated: bool
) -> None:
    events = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        events.append("selector")
        source.loc[:, "location_id"] = "forged"
        return relation.filter(relation["count"] > 0)

    def mapping(
        source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        events.append("mapping")
        assert set(source.location_id) == {"a", "b"}
        if repeated:
            assert list(source.source_location_id) == ["one", "two", "three"]
        source.loc[:, "location_id"] = "forged"
        pool.loc[:, "location_id"] = "forged"
        pool.loc[:, "count"] = 999
        pool.geometry = [Point(9, 9)] * len(pool)
        pool.attrs["nested"]["values"].append(99)
        pool.set_crs(3857, allow_override=True, inplace=True)
        return {"a": ("b",), "b": ("b",)}

    binding, invocation, target, _ = prepared(mapping, selector)
    if repeated:
        source = invocation.locations["declared_source"]
        data = source.frame.iloc[[0, 0, 1]].copy()
        data.insert(0, "source_location_id", ["one", "two", "three"])
        invocation = replace(
            invocation,
            parameters=None,
            locations={
                "declared_source": replace(
                    source, frame=data, source_scope=source.contract
                )
            },
        )
    output = frame().iloc[::-1].copy()
    output.attrs = {"nested": {"values": [1]}}
    scope, resolver, exact = resources(monkeypatch, target, output, events)
    validate = Mock(wraps=target.schema.validate)
    monkeypatch.setattr(target.schema, "validate", validate)
    with scope.activate():
        result = execute_operation(binding, invocation, resolver)
        forwarded = forward_correspondences(result, target.schema.metadata)
        assert list(forwarded.frame.source_location_id) == ["a", "b"]
        assert list(forwarded.frame.location_id) == ["b", "b"]
        assert forwarded.frame.geometry.to_list() == [Point(1, 1)] * 2
        assert forwarded.frame["count"].isna().all()
        assert forwarded.frame.crs is not None
        assert forwarded.frame.crs.to_epsg() == 4326
        assert result.targets.frame.attrs == output.attrs
        assert result.source_scope != result.targets.contract
        reused = resolver.resolve(
            {"later": LocationSelection(provider="managed", ids=["a", "b"])}
        )["later"]
        assert_geodataframe_equal(reused.frame, frame(), check_like=True)
        assert (
            validate.call_count == 1
        )  # Producer checks apply once, before expansion/reuse.
        execute_operation(binding, invocation, resolver)
        assert (
            events.count("query") == 2
        )  # Held records cannot prove complete membership.
    exact.assert_not_called()
    assert events == [
        "selector",
        "setup",
        "query",
        "mapping",
        "selector",
        "query",
        "mapping",
        "close",
    ]


@pytest.mark.parametrize("empty_source", [False, True])
@pytest.mark.parametrize("outcome", ["omit", "outside", "raise"])
def test_empty_selection_semantics(
    monkeypatch: pytest.MonkeyPatch, *, empty_source: bool, outcome: str
) -> None:
    events = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        events.append("selector")
        del source
        return relation

    def mapping(
        source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        events.append("mapping")
        del source
        assert_geodataframe_equal(pool, target.schema.empty_frame())
        if outcome == "raise":
            message = "no match"
            raise ValueError(message)
        return {} if outcome == "omit" else {"a": ("b",)}

    binding, invocation, target, _ = prepared(mapping, selector)
    if empty_source:
        source = invocation.locations["declared_source"]
        invocation = replace(
            invocation,
            parameters=None,
            locations={"declared_source": replace(source, frame=source.frame.iloc[:0])},
        )
    scope, resolver, exact = resources(
        monkeypatch, target, target.schema.empty_frame(), events
    )
    with scope.activate():
        if not empty_source and outcome != "omit":
            with pytest.raises(ValueError, match=r"no match|original candidates"):
                execute_operation(binding, invocation, resolver)
        else:
            result = execute_operation(binding, invocation, resolver)
            assert dict(result.groups) == ({} if empty_source else {"a": (), "b": ()})
            assert result.targets.frame.empty
    assert events == (
        [] if empty_source else ["selector", "setup", "query", "mapping", "close"]
    )
    exact.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "setup",
        "query",
        "decode",
        "ids",
        "duplicate",
        "attribute",
        "null",
        "geometry",
        "crs",
        "selector",
        "expression",
        "mapping",
    ],
)
def test_failures_propagate_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    events = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        events.append("selector")
        del source
        if failure == "selector":
            message = "selector failed"
            raise RuntimeError(message)
        return relation.limit(1) if failure == "expression" else relation

    def mapping(
        source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        events.append("mapping")
        del source, pool
        message = "mapping failed"
        raise RuntimeError(message)

    binding, invocation, target, _ = prepared(mapping, selector)
    output = frame()
    if failure == "ids":
        output["location_id"] = [1, 2]
    elif failure == "duplicate":
        output["location_id"] = ["a", "a"]
    elif failure == "attribute":
        output["count"] = ["bad", "bad"]
    elif failure == "null":
        output["location_id"] = [None, "b"]
    elif failure == "geometry":
        output.geometry = [Point(), Point(1, 1)]
    elif failure == "crs":
        output.set_crs(3857, allow_override=True, inplace=True)
    scope, resolver, exact = resources(
        monkeypatch, target, output, events, failure=failure
    )
    caplog.set_level("INFO", logger="lyra.io")
    expected = (
        RuntimeError
        if failure in {"selector", "mapping"}
        else ValueError
        if failure == "expression"
        else LocationAcquisitionError
        if failure in {"setup", "query", "decode"}
        else LocationProviderContractError
    )
    with pytest.raises(expected), scope.activate():
        execute_operation(binding, invocation, resolver)
    outcomes = [
        vars(record)["structured_fields"]["outcome"]
        for record in caplog.records
        if getattr(record, "structured_fields", {}).get("operation") == "select"
    ]
    assert outcomes == (
        []
        if failure in {"setup", "selector", "expression"}
        else ["success" if failure == "mapping" else "failure"]
    )
    assert ("mapping" in events) == (failure == "mapping")
    assert ("close" in events) == (failure not in {"selector", "expression"})
    exact.assert_not_called()


def test_held_target_outside_candidates_is_rejected_after_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mapping(
        source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        del source
        pool.loc[:, "location_id"] = "b"
        return {"a": ("b",)}

    binding, invocation, target, all_candidates = prepared(mapping, select_all)
    scope, resolver, exact = resources(monkeypatch, target, frame().iloc[:1], [])
    resolver.retain(all_candidates)
    with scope.activate(), pytest.raises(ValueError, match="original candidates"):
        execute_operation(binding, invocation, resolver)
    exact.assert_not_called()


def test_shared_executor_forwards_selected_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def mapping(
        source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        assert set(source.location_id) == {"a", "b"}
        assert set(pool.location_id) == {"a", "b"}
        return {"a": ("b",)}

    @metric(
        name="consume",
        code_revision="1",
        description="Consume selected targets",
        inputs={
            "seed": LocationDependency(provider=Places.id),
            "selected": LocationDependency("select_targets").bind(
                declared_source=Input("seed")
            ),
        },
        outputs={"data": pa.DataFrameSchema({"total": column(int)}, strict=True)},
    )
    def consume(seed: gpd.GeoDataFrame, selected: gpd.GeoDataFrame) -> pd.DataFrame:
        assert set(seed.location_id) == {"a", "b"}
        assert list(
            zip(selected.source_location_id, selected.location_id, strict=True)
        ) == [("a", "b")]
        assert selected.geometry.to_list() == [Point(1, 1)]
        return pd.DataFrame({"total": [len(selected)]})

    runner = LocalRunner.from_definitions(
        {
            "operations": plugin(mapping, select_all),
            "consumer": PluginDefinition(metrics=[consume]),
        },
        resource_config={"managed": ResourceConfig(config={"label": "fake"})},
    )
    events: list[str] = []
    _, _, exact = resources(
        monkeypatch, runner.catalog.provider_registry["managed"], frame(), events
    )
    result = runner.run(
        "consume", inputs={"seed": {"provider": Places.id, "ids": ["b", "a"]}}
    )
    assert result.tables["data"].total.to_list() == [1]
    assert events == ["setup", "query", "close"]
    exact.assert_not_called()


def test_selection_and_mapping_preserve_narrowed_source_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        seen.append(tuple(source.location_id))
        return relation

    def mapping(source: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> dict:
        # Each partition chooses its own target, regardless of pool growth.
        return {
            key: (key,) for key in source.location_id if key in set(pool.location_id)
        }

    binding, invocation, target, _ = prepared(mapping, selector)
    scope, resolver, exact = resources(monkeypatch, target, frame(), [])
    with scope.activate():
        complete = execute_operation(binding, invocation, resolver)
        partitioned = {}
        for identifier in ("b", "a"):
            narrowed = invocation.select_primary(
                "declared_source", frozenset({identifier})
            )
            result = execute_operation(binding, narrowed, resolver)
            assert set(result.groups) == {identifier}
            partitioned.update(result.groups)
            assert narrowed.original().locations["declared_source"].ids == ("a", "b")
        assert partitioned == dict(complete.groups)
    assert seen == [("a", "b"), ("b",), ("a",)]
    exact.assert_not_called()
