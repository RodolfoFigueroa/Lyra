from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from typing_extensions import override

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.locations import LocationContract
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.stored_operation import StoredOperation
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput, input_fingerprint
from lyra.runtime.planning.computation import executable_contract
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from tests.provider_execution_helpers import provider_execution_definitions
from tests.provider_schema_helpers import declaration, frame

SCHEMA = LocationOutputSchema(declaration(), id_column="location_id")
ORIGIN = Origin(id="external", revision="1", description="Initial description")


def contract(scope: ProviderScope) -> LocationContract:
    return LocationContract(
        family=SCHEMA.family(scope.provider_id, "record"),
        source=scope.provider_id,
        geographical_edition="provider",
        meaning="Provider records",
        provider_scope=scope,
    )


def definitions(
    scope: ProviderScope, events: list[str], alias: str = "records"
) -> dict[str, PluginDefinition]:
    class Records(LocationProvider):
        id = scope.provider_id
        code_revision = scope.code_revision
        origins = tuple(scope.origins)
        schema = SCHEMA.schema

        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def close(self) -> None:
            events.append("close")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append("resolve")
            result = frame()
            result.attrs["provider_scope"] = {"provider_id": "forged"}
            return result[result.location_id.isin(ids)]

    output = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Count", metadata={"unit": None})},
        strict=True,
    )

    @metric(
        cache="full",
        name="source",
        code_revision="1",
        description="Source",
        inputs={"places": LocationDependency(provider=scope.provider_id)},
        outputs={"result": output},
    )
    def source(places: gpd.GeoDataFrame) -> pd.DataFrame:
        events.append("source")
        native = places
        assert set(native.columns) == {"location_id", "count", "geometry"}
        assert native["count"].iloc[0] == 1
        return pd.DataFrame({"value": [len(places)]})

    @metric(
        cache="full",
        name="root",
        code_revision="1",
        description="Root",
        inputs={
            "places": LocationDependency(provider=scope.provider_id),
            "data": Dependency("result", producer="source"),
        },
        outputs={"result": output},
    )
    def root(places: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        events.append("root")
        assert len(places) == 1
        return data

    return {
        "test": PluginDefinition(metrics=[source, root], resources={alias: Records})
    }


def inputs() -> dict[str, object]:
    return {"places": {"provider": "records", "ids": ["a"]}}


def request() -> dict[str, object]:
    return {
        "metric": "root",
        "inputs": {
            "places": {
                "provider": "records",
                "ids": ["a"],
            }
        },
    }


@pytest.mark.parametrize("provider_id", ["records", " ", " records "])
def test_scope_retained_by_preparation_execution_and_artifact_round_trip(
    tmp_path: Path,
    provider_id: str,
) -> None:
    scope = ProviderScope(provider_id=provider_id, code_revision="1", origins=[ORIGIN])
    events: list[str] = []
    runner = LocalRunner.from_definitions(definitions(scope, events))
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "root",
            "inputs": {"places": {"provider": provider_id, "ids": ["a"]}},
        },
    )
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert all(
        binding.contract.provider_scope == scope
        for binding in restored.revisions.bindings
    )
    executor = GraphExecutor(
        runner.catalog, dict(runner.catalog.implementations), Mock()
    )
    locations = executor.acquire_sources(restored)["places"]
    assert events == ["setup", "resolve", "close"]
    cache = TableCache(tmp_path)
    reference = cache.write_operation(
        "scope-roundtrip",
        StoredOperation(
            groups={identifier: (identifier,) for identifier in locations.ids},
            source_scope=locations.contract,
            target_scope=locations.contract,
        ),
    )
    assert reference is not None
    restored_locations = cache.read_operation("scope-roundtrip")
    assert restored_locations is not None
    assert restored_locations.value.source_scope == locations.contract
    assert restored_locations.value.target_scope == locations.contract
    assert restored_locations.value.groups == {
        identifier: (identifier,) for identifier in locations.ids
    }
    events.clear()
    assert executor.execute(restored).outputs["result"].value.tolist() == [1]
    assert events == ["setup", "resolve", "source", "root", "close"]


@pytest.mark.parametrize("provider_id", ["records", " ", " records "])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("development", [False, True])
def test_opaque_provider_ids_through_managed_execution(
    provider_id: str, *, empty: bool, development: bool
) -> None:
    scope = ProviderScope(provider_id=provider_id, origins=[ORIGIN])
    events: list[str] = []
    providers = definitions(scope, events)["test"].resources
    expected = frame().iloc[:0] if empty else frame()
    expected = expected[list(SCHEMA.schema.columns)]

    @metric(
        cache="full",
        name="inspect_records",
        code_revision="1",
        description="Count native provider records",
        inputs={"places": LocationDependency(provider=provider_id)},
        outputs={
            "result": pa.DataFrameSchema(
                {"count": pa.Column(int, description="Count", metadata={"unit": None})},
                strict=True,
            )
        },
    )
    def inspect_records(places: gpd.GeoDataFrame) -> pd.DataFrame:
        pd.testing.assert_frame_equal(places, expected)
        assert places.crs == expected.crs
        events.append("metric")
        return pd.DataFrame({"count": [len(places)]})

    runner = LocalRunner.from_definitions(
        {"test": PluginDefinition(metrics=[inspect_records], resources=providers)}
    )
    selected = {
        "places": {"provider": provider_id, "ids": expected.location_id.tolist()}
    }
    plan = prepare_execution(
        runner.catalog, {"metric": "inspect_records", "inputs": selected}
    )
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert restored.inputs[0].binding.source.provider_scope == scope
    assert restored.request.inputs["places"].provider == provider_id
    assert events == []
    run = runner.run_development if development else runner.run
    result = run("inspect_records", inputs=selected)
    assert result.tables["result"]["count"].tolist() == [len(expected)]
    assert events == (["metric"] if empty else ["setup", "resolve", "metric", "close"])


@pytest.mark.parametrize("development", [False, True])
def test_scope_metadata_and_resource_alias_preserve_metric_cache(
    tmp_path: Path, *, development: bool
) -> None:
    events: list[str] = []
    original = ProviderScope(
        provider_id="records",
        code_revision="1",
        origins=[ORIGIN, Origin(id="elevation")],
    )
    revised = ProviderScope(
        provider_id="records",
        code_revision="1",
        origins=[
            Origin(id="elevation"),
            Origin(id="external", revision="1", description="Edited description"),
            ORIGIN,
        ],
    )
    for scope, alias in ((original, "first"), (revised, "second")):
        plugins = definitions(scope, events, alias)
        if alias == "second":
            plugins = {"different-owner": plugins["test"]}
        runner = LocalRunner.from_definitions(plugins, artifact_dir=tmp_path)
        run = runner.run_development if development else runner.run
        result = run("root", inputs=inputs())
        assert result.tables["result"].value.tolist() == [1]
    assert events.count("source") == 1
    assert events.count("root") == 1
    assert events.count("setup") == events.count("close") == 2


@pytest.mark.parametrize(
    "changed",
    [
        ProviderScope(provider_id="other", code_revision="1", origins=[ORIGIN]),
        ProviderScope(provider_id="records", code_revision="2", origins=[ORIGIN]),
        ProviderScope(
            provider_id="records",
            code_revision="1",
            origins=[Origin(id="external", revision="2")],
        ),
    ],
)
def test_scope_changes_separate_equal_bare_ids_and_reject_stale_acceptance(
    tmp_path: Path, changed: ProviderScope
) -> None:
    original = ProviderScope(provider_id="records", code_revision="1", origins=[ORIGIN])
    first = NativeLocationInput(frame(), contract(original).reference)
    second = NativeLocationInput(frame(), contract(changed).reference)
    assert first.ids == second.ids
    assert first.contract != second.contract
    assert input_fingerprint(first) != input_fingerprint(second)
    assert contract(original).unit_identity("a") != contract(changed).unit_identity("a")
    events: list[str] = []
    old = LocalRunner.from_definitions(
        definitions(original, events), artifact_dir=tmp_path
    )
    plan = prepare_execution(old.catalog, request())
    old.run("root", inputs=inputs())
    events.clear()
    current = LocalRunner.from_definitions(
        definitions(changed, events), artifact_dir=tmp_path
    )
    executor = GraphExecutor(
        current.catalog, dict(current.catalog.implementations), Mock()
    )
    with pytest.raises(GraphExecutionError):
        executor.execute(plan)
    assert events == []
    current.run(
        "root", inputs={"places": {"provider": changed.provider_id, "ids": ["a"]}}
    )
    assert events == ["setup", "resolve", "source", "root", "close"]


def test_scope_projection_ignores_editorial_metadata() -> None:
    original = ProviderScope(provider_id="records", origins=[ORIGIN])
    changed = ProviderScope(
        provider_id="records",
        origins=[Origin(id="external", revision="1", description="Other")],
    )
    assert executable_contract(contract(original)) == executable_contract(
        contract(changed)
    )
    assert (
        contract(original).reference.identity_payload
        == contract(changed).reference.identity_payload
    )


def test_cache_scope_and_direct_metric_origin_identity(tmp_path: Path) -> None:
    events: list[str] = []
    inputs = {"places": {"provider": "source", "ids": ["a"]}}
    original = LocalRunner.from_definitions(
        provider_execution_definitions(events), artifact_dir=tmp_path
    )
    plan = prepare_execution(original.catalog, {"metric": "count", "inputs": inputs})
    for revision, alias, metric_revision in [
        ("1", "source_binding", "1"),
        ("1", "renamed", "1"),
        ("2", "renamed", "1"),
        ("2", "renamed", "2"),
    ]:
        runner = LocalRunner.from_definitions(
            provider_execution_definitions(
                events, revision=revision, alias=alias, metric_revision=metric_revision
            ),
            artifact_dir=tmp_path,
        )
        runner.run("count", inputs=inputs)
    assert events.count("metric") == 3
    assert events.count("resolve:source") == 4
    executor = GraphExecutor(
        runner.catalog, dict(runner.catalog.implementations), Mock()
    )
    events.clear()
    with pytest.raises(GraphExecutionError):
        executor.execute(plan)
    assert events == []


def test_equal_bare_ids_retain_distinct_provider_scopes() -> None:
    runner = LocalRunner.from_definitions(provider_execution_definitions([]))
    plans = [
        prepare_execution(
            runner.catalog,
            {
                "metric": "count",
                "inputs": {"places": {"provider": provider, "ids": ["a"]}},
            },
        )
        for provider in ("source", "target")
    ]
    executor = GraphExecutor(
        runner.catalog, dict(runner.catalog.implementations), Mock()
    )
    resolved = [executor.acquire_sources(plan)["places"] for plan in plans]
    assert resolved[0].ids == resolved[1].ids
    assert resolved[0].contract != resolved[1].contract
    for provider, value in zip(("source", "target"), resolved, strict=True):
        assert value.contract is not None
        assert value.contract.provider_scope is not None
        assert value.contract.provider_scope.provider_id == provider
