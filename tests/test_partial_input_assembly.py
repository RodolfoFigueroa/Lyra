"""Related selections restrict full operation groups without losing shared targets."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import LocationInputs
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("development", [False, True])
def test_related_shared_target_groups(
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    policy: CachePolicy,
    *,
    development: bool,
) -> None:
    @location_operation(
        name="mapping",
        description="Shared targets",
        cache=policy,
        inputs={"source": LocationDependency(provider="source")},
        target_provider="target",
    )
    def mapping(source: gpd.GeoDataFrame) -> dict:
        return dict.fromkeys(source.location_id, ("shared",))

    @metric(
        name="consumer",
        description="Related pairs",
        cache="per_location",
        inputs={
            "source": LocationDependency(provider="source", primary=True),
            "mapped": LocationDependency(operation="mapping").bind(
                source=Input("source")
            ),
        },
        outputs={"data": schema()},
    )
    def consumer(source: gpd.GeoDataFrame, mapped: gpd.GeoDataFrame) -> pd.DataFrame:
        assert set(mapped.source_location_id) == set(source.location_id)
        return pd.DataFrame(
            {"location_id": source.location_id, "row_key": "one", "value": 1}
        )

    definitions = provider_execution_definitions([])
    definitions["consumer"] = PluginDefinition(
        metrics=[consumer], location_operations=[mapping]
    )
    snapshots = []
    for iteration, ids in enumerate((["a", "b"], ["b", "c"], ["b"])):
        for provider in definitions["providers"].resources.values():
            monkeypatch.setattr(provider, "description", f"Description {iteration}")
        runner = LocalRunner.from_definitions(
            definitions, result_store_path=tmp_path / "values"
        )
        run = runner.run_development if development else runner.run
        result = run("consumer", inputs={"source": {"provider": "source", "ids": ids}})
        result.metadata.store_path.touch()
        with ResultStore.open(result.metadata.store_path) as store:
            projection = store.describe(result.metadata.result_ref)
            snapshots.append((result.metadata.result_ref, projection.model_dump()))
            captured = projection.location_inputs["mapped"]
            assert captured.provider_scope.description in {
                f"Description {index}" for index in range(iteration + 1)
            }
            assert captured.source_scope is not None
            assert (
                captured.source_scope.description == captured.provider_scope.description
            )
            for reference, snapshot in snapshots:
                assert store.describe_result(reference).model_dump() == snapshot
            assert projection.model_dump() == result.metadata.model_dump(
                exclude={"store_path"}
            )
            result.metadata.location_inputs["mapped"].table_schema.columns.clear()
            result.metadata.outputs["data"].table_schema.columns.clear()
            assert store.describe(result.metadata.result_ref) == projection
            pairs = store.read_location_input(result.metadata.result_ref, "mapped")
        assert set(zip(pairs.source_location_id, pairs.location_id, strict=True)) == {
            (key, "shared") for key in ids
        }

    with SQLiteStore(result.metadata.store_path) as sqlite:
        retained = LocationInputs(sqlite)
        with sqlite.engine.connect() as connection:
            for row in database.rows["metric_inputs"]:
                bindings = retained.bindings(connection, row["value_key"])
                for group in bindings.groups:
                    selected = bindings.selected((group,))
                    pairs = retained.frame(connection, selected.selections["mapped"])
                    assert set(pairs.source_location_id) == {group}


def test_sibling_inputs_follow_shared_owners_through_retention(
    tmp_path: Path, database: Database
) -> None:
    @location_operation(
        name="owners",
        description="Select owners",
        cache="per_location",
        inputs={"source": LocationDependency(provider="source")},
        target_provider="target",
    )
    def owners(source: gpd.GeoDataFrame) -> dict:
        return {
            key: {"a": ("X", "Y"), "b": ("Y",), "c": ()}[key]
            for key in source.location_id
        }

    @location_operation(
        name="siblings",
        description="Select sibling records",
        cache="per_location",
        inputs={"source": LocationDependency(provider="source")},
        target_provider="target",
    )
    def siblings(source: gpd.GeoDataFrame) -> dict:
        return dict.fromkeys(source.location_id, ("shared",))

    @metric(
        name="consumer",
        description="Related owners",
        cache="per_location",
        inputs={
            "source": LocationDependency(provider="source"),
            "owners": LocationDependency(operation="owners", primary=True).bind(
                source=Input("source")
            ),
            "siblings": LocationDependency(operation="siblings").bind(
                source=Input("source")
            ),
            "independent": LocationDependency(provider="source"),
        },
        outputs={"data": schema()},
    )
    def consumer(
        source: gpd.GeoDataFrame,
        owners: gpd.GeoDataFrame,
        siblings: gpd.GeoDataFrame,
        independent: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert set(source.location_id) == {"a", "b"}
        assert set(independent.location_id) == {"a", "b", "c"}
        assert set(
            zip(siblings.source_location_id, siblings.location_id, strict=True)
        ) == {("a", "shared"), ("b", "shared")}
        return pd.DataFrame(
            {"location_id": owners.location_id.unique(), "row_key": "one", "value": 1}
        )

    runner = LocalRunner.from_definitions(
        {
            "providers": provider_execution_definitions([])["providers"],
            "consumer": PluginDefinition(
                metrics=[consumer], location_operations=[owners, siblings]
            ),
        },
        result_store_path=tmp_path / "values",
    )
    selection = {"provider": "source", "ids": ["a", "b", "c"]}
    result = runner.run(
        "consumer", inputs={"source": selection, "independent": selection}
    )
    assert set(result.tables["data"].location_id) == {"X", "Y"}
    groups = {}
    with (
        SQLiteStore(result.metadata.store_path) as store,
        store.engine.connect() as connection,
    ):
        retained = LocationInputs(store)
        for row in database.rows["metric_inputs"]:
            bindings = retained.bindings(connection, row["value_key"])
            for group in bindings.groups:
                selected = bindings.selected((group,)).selections
                pairs = retained.frame(connection, selected["siblings"])
                groups[group] = set(
                    zip(pairs.source_location_id, pairs.location_id, strict=True)
                )
                assert set(
                    retained.frame(connection, selected["independent"]).location_id
                ) == {"a", "b", "c"}
    assert groups == {"X": {("a", "shared")}, "Y": {("a", "shared"), ("b", "shared")}}
