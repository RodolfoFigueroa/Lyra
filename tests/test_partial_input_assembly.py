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
