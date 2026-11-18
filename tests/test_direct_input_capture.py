"""Fresh metric inputs survive mutation, disabled caches and writer closure."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.errors import (
    ExecutionPersistenceError,
    LocationInputUnavailableError,
)
from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.provider_scope import RecordReferences
from lyra.contracts.results import LocationInputDescription
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.input_capture import (
    capture_inputs,
    capture_skipped_inputs,
    remember_inputs,
)
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputBindings, InputSelection, LocationInputs
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.bindings import BoundLocation
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.provider_records import ProviderRecords, provider_registration
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk import LocationDependency, LocationProvider, PluginDefinition, metric
from lyra.sdk.interventions import Intervention, accept_interventions
from lyra.server import worker
from tests.metric_execution_helpers import schema
from tests.provider_schema_helpers import declaration, frame
from tests.queue_helpers import accepted_envelope
from tests.sqlite_helpers import Database, database, fail_insert
from tests.worker_graph_integration_helpers import install_definitions, native_job

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database", "worker_state")


def definitions(
    events: list, *, cache: bool, metric_cache: CachePolicy = "off"
) -> dict[str, PluginDefinition]:
    class Places(LocationProvider):
        id = "places"
        description = "Captured places"
        schema = declaration()

        @override
        def existing_ids(self, ids: list[str]) -> set[str]:
            events.append(("membership", set(ids)))
            return set(ids)

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(set(ids))
            result = frame().iloc[[0] * len(ids)].reset_index(drop=True)
            result["location_id"] = ids
            return result

    Places.cache = cache

    @metric(
        name="count",
        cache=metric_cache,
        description="Independent reference inputs with no primary",
        inputs={
            "left": LocationDependency(provider="places"),
            "right": LocationDependency(provider="places"),
        },
        outputs={"rows": declaration().remove_columns(["location_id", "geometry"])},
    )
    def count(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
        for value in (left, right):
            value.loc[:, "location_id"] = "changed"
            value.loc[:, "count"] = 99
            value.geometry = value.geometry.translate(xoff=50)
            value.set_crs(3857, allow_override=True, inplace=True)
            value.attrs["scope"] = "forged"
        return pd.DataFrame({"count": pd.Series([], dtype="Int64")})

    return {"p": PluginDefinition(metrics=[count], resources={"places": Places})}


@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_direct_references(
    tmp_path: Path, *, cache: bool, development: bool, empty: bool
) -> None:
    events: list = []
    path = tmp_path / "inputs.sqlite"
    runner = LocalRunner.from_definitions(
        definitions(events, cache=cache), result_store_path=path
    )
    run = runner.run_development if development else runner.run
    inputs = {
        "left": {"provider": "places", "ids": [] if empty else ["b", "a"]},
        "right": {"provider": "places", "ids": ["x", "y"]},
    }
    result = run("count", inputs=inputs)
    assert result.tables["rows"].empty
    assert set(result.metadata.location_inputs) == {"left", "right"}
    assert result.metadata.location_inputs["left"].availability == "available"
    assert (
        result.metadata.location_inputs["left"].provider_scope.description
        == "Captured places"
    )
    events.clear()
    run("count", inputs=inputs)
    assert bool(events) is not cache
    del runner
    path.touch()  # The database fixture supplies all connections.
    with ResultStore.open(path) as reader:
        for name, ids in (
            ("left", set() if empty else {"a", "b"}),
            ("right", {"x", "y"}),
        ):
            saved = reader.read_location_input(result.metadata.result_ref, name)
            assert set(saved.location_id) == ids
            assert saved.crs == "EPSG:4326"
            assert saved["count"].tolist() == [1] * len(ids)
            assert "row_key" not in saved
            saved.loc[:, "count"] = 100
            result.metadata.location_inputs[name].table_schema.columns.clear()
            again = reader.read_location_input(result.metadata.result_ref, name)
            assert again["count"].tolist() == [1] * len(ids)
            assert (
                reader.describe_result(result.metadata.result_ref)
                .location_inputs[name]
                .provider_scope.description
                == "Captured places"
            )
            assert (
                reader.describe_result(result.metadata.result_ref)
                .location_inputs[name]
                .table_schema.columns
            )


@pytest.mark.parametrize(
    "boundary", [ProviderRecords, LocationInputs, MetricTables, ResultDescriptions]
)
def test_required_input_publication_failure(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch, boundary: type
) -> None:
    runner = LocalRunner.from_definitions(
        definitions([], cache=False), result_store_path=tmp_path / "failure.sqlite"
    )
    if boundary is ResultDescriptions:
        fail_insert(
            database, monkeypatch, "final_results", RuntimeError("failed write")
        )
    else:
        monkeypatch.setattr(
            boundary, "publish", Mock(side_effect=RuntimeError("failed write"))
        )
    with pytest.raises(ExecutionPersistenceError):
        runner.run(
            "count",
            inputs={
                name: {"provider": "places", "ids": ["a"]} for name in ("left", "right")
            },
        )
    assert not database.rows.get("final_results")


@pytest.mark.parametrize(
    "boundary", [ProviderRecords, LocationInputs, MetricTables, ResultDescriptions]
)
def test_worker_input_publication_failure(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch, boundary: type
) -> None:
    _, runtime = install_definitions(
        tmp_path, monkeypatch, definitions([], cache=False)
    )
    worker.refresh_runner_registry("lightweight", config=runtime.config)
    native = native_job(monkeypatch, "count")
    envelope = accepted_envelope(
        job_id=native.id,
        metric="count",
        inputs={
            name: {"provider": "places", "ids": ["a"]} for name in ("left", "right")
        },
    )
    if boundary is ResultDescriptions:
        fail_insert(
            database, monkeypatch, "final_results", RuntimeError("failed write")
        )
    else:
        monkeypatch.setattr(
            boundary, "publish", Mock(side_effect=RuntimeError("failed write"))
        )
    with pytest.raises(GraphExecutionError) as caught:
        worker.run_metric_task(envelope.model_dump(mode="json"))
    assert isinstance(caught.value.__cause__, ExecutionPersistenceError)
    assert native.meta["failure"]
    assert not native.meta["execution"]["succeeded"]
    assert not database.rows.get("final_results")


@pytest.mark.parametrize("empty", [False, True])
def test_resolved_native_capture(tmp_path: Path, *, empty: bool) -> None:
    local = LocalRunner.from_definitions(
        definitions([], cache=False, metric_cache="full")
    )
    catalog = local.catalog
    contract = catalog.provider_registry["places"].contract.reference
    original = frame().iloc[:0] if empty else frame()
    invocation = catalog.implementations["p"].prepare_resolved_invocation(
        "count",
        {name: NativeLocationInput(original, contract) for name in ("left", "right")},
    )
    with SQLiteStore(tmp_path / "native.sqlite") as store:
        executor = GraphExecutor(
            catalog, catalog.implementations, Mock(), store=store, retain_result=False
        )
        result = executor.execute(invocation)
        key = result.completions[0].computations[0][1]
        retained = LocationInputs(store)
        with store.engine.connect() as connection:
            bindings = retained.bindings(connection, key)
            saved = retained.frame(connection, bindings.selected().selections["left"])
        assert set(saved.location_id) == (set() if empty else {"a", "b"})
        assert saved.crs == "EPSG:4326"
        pd.testing.assert_series_equal(
            saved["count"], original["count"], check_index=False
        )


@pytest.mark.parametrize("development", [False, True])
def test_complete_hit_restores_inputs_without_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, development: bool
) -> None:
    events: list = []
    path = tmp_path / "reuse.sqlite"
    plugins = definitions(events, cache=False, metric_cache="full")
    inputs = {
        "left": {"provider": "places", "ids": ["a", "b"]},
        "right": {"provider": "places", "ids": ["shared"]},
    }
    first = LocalRunner.from_definitions(plugins, result_store_path=path).run(
        "count", inputs=inputs
    )
    events.clear()
    monkeypatch.setattr(
        plugins["p"].resources["places"], "description", "Edited places"
    )
    runner = LocalRunner.from_definitions(plugins, result_store_path=path)
    with monkeypatch.context() as patch:
        patch.setattr(LocationInputs, "rows", Mock(side_effect=AssertionError))
        run = runner.run_development if development else runner.run
        reused = run("count", inputs=inputs)
    assert not events
    assert reused.tables["rows"].empty
    assert reused.metadata.result_ref != first.metadata.result_ref
    path.touch()
    with ResultStore.open(path) as reader:
        for result in (first, reused):
            assert (
                reader.describe_result(result.metadata.result_ref)
                .location_inputs["left"]
                .provider_scope.description
                == "Captured places"
            )
        for name, ids in (("left", {"a", "b"}), ("right", {"shared"})):
            saved = reader.read_location_input(reused.metadata.result_ref, name)
            assert set(saved.location_id) == ids
            assert saved.crs == "EPSG:4326"


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("intervened", [False, True])
def test_skipped_input_inventory(
    tmp_path: Path, *, development: bool, intervened: bool
) -> None:

    events: list = []

    @metric(
        name="skipped",
        description="Empty primary",
        cache="per_location",
        inputs={
            "primary": LocationDependency(provider="places", primary=True),
            "reference": LocationDependency(provider="places"),
        },
        outputs={"data": schema()},
    )
    def skipped(primary: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        assert not reference.empty
        return pd.DataFrame(
            {"location_id": primary.location_id, "row_key": "one", "value": 1}
        )

    plugins = definitions(events, cache=False)
    plugins["skipped"] = PluginDefinition(metrics=[skipped])
    path = tmp_path / "skipped.sqlite"
    runner = LocalRunner.from_definitions(plugins, result_store_path=path)
    run = runner.run_development if development else runner.run
    inputs = {
        "primary": {"provider": "places", "ids": []},
        "reference": {"provider": "places", "ids": ["x"]},
    }
    changes = (
        [Intervention(catalog="places", id="plan", revision="1", remove=["outside"])]
        if intervened
        else []
    )
    result = run("skipped", inputs=inputs, interventions=changes)
    assert events == ([("membership", {"outside"})] if intervened else [])
    if intervened:
        (validation,) = result.report.intervention_validations
        assert validation.membership_lookup == "executed"
        assert (
            result.metadata.location_inputs["reference"].provider_scope
            == validation.scope
        )
    path.touch()
    with ResultStore.open(path) as reader:
        ref = result.metadata.result_ref
        empty = reader.read_location_input(ref, "primary")
        assert empty.empty
        assert empty.geometry.name == "geometry"
        assert empty.crs is not None
        assert empty.crs.to_epsg() == 4326
        assert str(empty["count"].dtype) == "Int64"
        assert (
            reader.describe_result(ref).location_inputs["reference"].availability
            == "not_materialized"
        )
        assert (
            reader.describe_result(ref)
            .location_inputs["reference"]
            .provider_scope.description
            == "Captured places"
        )
        with pytest.raises(LocationInputUnavailableError):
            reader.read_location_input(ref, "reference")
        run(
            "count",
            inputs={
                name: {"provider": "places", "ids": ["x"]} for name in ("left", "right")
            },
        )
        assert (
            reader.describe_result(ref).location_inputs["reference"].availability
            == "not_materialized"
        )


@pytest.mark.parametrize("complete", [False, True])
def test_skipped_matching_selection(tmp_path: Path, *, complete: bool) -> None:
    runner = LocalRunner.from_definitions(definitions([], cache=False))
    catalog = runner.catalog
    provider = catalog.provider_registry["places"]
    binding = BoundLocation(provider.contract.reference, ("a", "b"))
    native = NativeLocationInput(
        frame() if complete else frame().iloc[:1], provider.contract.reference
    )
    invocation = Mock(
        bound_use=Mock(locations={"reference": binding}),
        locations={"reference": native},
    )
    with SQLiteStore(tmp_path / "matching.sqlite") as store:
        ProviderRecords(store).publish(provider, frame())
        selection = InputSelection(
            description=LocationInputDescription(
                kind="provider",
                provider_scope=provider.scope,
                table_schema=provider.schema.metadata,
                availability="available",
            ),
            registration=provider_registration(provider),
            records=("a", "b"),
        )
        key = LocationInputs(store).publish(selection)
        matching: dict[str, tuple[str, ...]] = {}
        remember_inputs(
            invocation,
            {
                "computation": InputBindings(
                    names=("reference",), selections={"reference": (key,)}
                )
            },
            matching,
        )
        inventory = capture_skipped_inputs(
            store, catalog, Mock(locations={"reference": binding}), matching
        )
        descriptions = LocationInputs(store).descriptions
        with store.engine.connect() as connection:
            assert descriptions(connection, inventory)["reference"].availability == (
                "available" if complete else "not_materialized"
            )
        other = BoundLocation(
            provider.contract.reference.model_copy(
                update={
                    "provider_scope": provider.scope.model_copy(
                        update={"code_revision": "different"}
                    )
                }
            ),
            ("a", "b"),
        )
        assert other.identity not in matching


def capture_native_inputs(
    store: SQLiteStore, catalog: CompiledCatalog, value: NativeLocationInput
) -> InputBindings:
    invocation = catalog.implementations["p"].prepare_resolved_invocation(
        "count", {"left": value, "right": value.select_ids(frozenset())}
    )
    table = invocation.arguments()["left"]
    assert isinstance(table, gpd.GeoDataFrame)
    assert list(table.columns) == list(value.frame.columns)
    table.loc[:, "count"] = 44
    header = Header(value_key="count", node_id="count", output_scope="unpartitioned")
    return capture_inputs(store, catalog, invocation, ((header, ()),), enabled=True)[
        "count"
    ]


def test_effective_input_capture_preserves_base_selection(tmp_path: Path) -> None:
    events = []
    path = tmp_path / "store"
    catalog = LocalRunner.from_definitions(
        definitions(events, cache=False), result_store_path=path
    ).catalog
    provider = catalog.provider_registry["places"]
    mixed = frame()
    mixed["location_id"] = ["a", "new"]
    change = accept_interventions(
        [
            Intervention(
                catalog="places",
                id="plan",
                revision="r1",
                add=mixed.iloc[[1]],
                remove=["b"],
            )
        ],
        {"places": provider.schema},
    )[0]
    effective = change.effective_scope(provider.scope)

    with SQLiteStore(path) as store:
        original = capture_native_inputs(
            store,
            catalog,
            NativeLocationInput(
                provider.schema.validate(frame()), provider.contract.reference
            ),
        )
        reusable = ProviderRecords(store).lookup(
            provider,
            ["a"],
            required=True,
            references=RecordReferences(effective, change.added_ids),
        )
        assert reusable["count"].tolist() == [1]
        value = NativeLocationInput(
            provider.schema.validate(mixed),
            provider.contract.reference.model_copy(
                update={"provider_scope": effective}
            ),
            added_ids=change.added_ids,
        )
        selected = capture_native_inputs(store, catalog, value)
    with SQLiteStore(path) as reopened, reopened.engine.connect() as connection:
        inputs = LocationInputs(reopened)
        ordinary = inputs.frame(connection, original.selections["left"])
        assert ordinary.location_id.tolist() == ["a", "b"]
        assert ordinary.loc[0, "count"] == 1
        current = inputs.frame(connection, selected.selections["left"])
        assert current.location_id.tolist() == ["a", "new"]
        assert current.loc[0, "count"] == 1
        assert current["count"].isna().tolist() == [False, True]
        assert current.crs == ordinary.crs
        descriptions = inputs.descriptions(connection, selected)
        assert (
            descriptions["left"].provider_scope
            == descriptions["right"].provider_scope
            == effective
        )
        assert descriptions["left"].provider_scope.model_dump() == {
            "base": provider.scope.model_dump(),
            "intervention": {"catalog": "places", "id": "plan", "revision": "r1"},
        }
        assert inputs.frame(connection, selected.selections["right"]).empty
        assert ProviderRecords(reopened).lookup(provider, ["new"]).empty
    assert not events
