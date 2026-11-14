"""Combined fidelity through runtime selections, standalone reads, and cache hits."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field
from typing_extensions import override

from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import LocationDependency, LocationProvider, MetricParameters
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import worker
from tests.frame_assertions import assert_logical_equal as assert_equal
from tests.metric_execution_helpers import schema as scoped_schema
from tests.provider_schema_helpers import declaration as provider_schema
from tests.provider_schema_helpers import frame as provider_frame
from tests.queue_helpers import accepted_envelope, read_retained
from tests.sqlite_helpers import Database, database
from tests.storage_data import native_declarations as declarations
from tests.storage_data import native_frame as frame
from tests.worker_graph_integration_helpers import install_definitions, native_job

__all__ = ["database"]


@pytest.mark.usefixtures("worker_state")
@pytest.mark.parametrize("state", ["empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_nullable_results_across_execution_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    database: Database,
    *,
    spatial: bool,
) -> None:
    schema = declarations(spatial=spatial).remove_columns(["category"])
    produced = frame(state, spatial=spatial).drop(columns="category")
    expected = produced[list(schema.columns)].reset_index(drop=True)

    @metric(
        name="root",
        description="Nullable native outputs",
        code_revision="1",
        outputs={"first": schema, "second": schema},
    )
    def root() -> dict[str, pd.DataFrame]:
        return {"first": produced, "second": produced}

    definitions = {"nullable": PluginDefinition(metrics=[root])}
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    runner = LocalRunner.from_definitions(definitions)
    local = runner.run("root")
    local_keys = {row["value_key"] for row in database.rows["stored_results"]}
    development = runner.run_development("root")
    envelope = accepted_envelope(
        job_id="nullable-job",
        metric="root",
        inputs={},
        catalog=runtime.catalog.compiled,
    )
    native = native_job(monkeypatch)
    native.id = envelope.job_id
    result = worker.run_metric_task(envelope.model_dump(mode="json"))
    key = str(result["result_ref"])
    assert {row["value_key"] for row in database.rows["stored_results"]} == local_keys
    assert len(local_keys) == 1
    assert len({key, local.metadata.result_ref, development.metadata.result_ref}) == 3
    assert (
        read_retained(key).metadata.outputs
        == local.metadata.outputs
        == development.metadata.outputs
    )
    for tables in (local.tables, development.tables, read_retained(key).tables):
        for output in ("first", "second"):
            assert_equal(tables[output], expected)
        del tables["first"]["signed"]
        assert_equal(tables["second"], expected)
    assert_equal(read_retained(key).tables["first"], expected)
    assert_equal(produced, expected)


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
@pytest.mark.parametrize("spatial", [False, True])
def test_metric_fidelity_reaches_consumer(
    tmp_path: Path, state: str, *, spatial: bool
) -> None:
    schema = declarations(spatial=spatial).remove_columns(["category"])
    for name, column in schema.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    produced = frame(state, spatial=spatial).drop(columns="category")
    expected = produced[list(schema.columns)].reset_index(drop=True)
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"rows": schema},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        return produced

    @metric(
        cache="full",
        code_revision="1",
        name="consumer",
        description="Consumer",
        outputs={"result": schema},
        inputs={"rows": Dependency("rows", producer="source")},
    )
    def consumer(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("consumer")
        assert_equal(rows, expected)
        return rows

    runner = LocalRunner.from_definitions(
        {"acceptance": PluginDefinition(metrics=[source, consumer])},
        result_store_path=tmp_path / "results.sqlite3",
    )
    assert_equal(runner.run("source", inputs={}).tables["rows"], expected)
    assert calls == ["source"]
    assert_equal(runner.run("consumer", inputs={}).tables["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(runner.run("consumer", inputs={}).tables["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(produced, frame(state, spatial=spatial).drop(columns="category"))


class Scale(MetricParameters):
    factor: int = Field(default=1, description="Value multiplier")


def scope_value(scope: ProviderScope) -> int:
    return int(scope.code_revision or "1") * 10 + sum(
        int(origin.revision or "0") for origin in scope.origins
    )


def scoped_plugin(
    scope: ProviderScope, calls: list[str], name: str = "findings"
) -> PluginDefinition:
    class Places(LocationProvider):
        id = scope.provider_id
        code_revision = scope.code_revision
        origins = tuple(scope.origins)
        schema = provider_schema()

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            result = provider_frame().query("location_id in @ids").copy()
            result["count"] = pd.array(
                [scope_value(scope)] * len(result), dtype="Int64"
            )
            return result

    @metric(
        name=name,
        description="Scoped acceptance values",
        cache="full",
        inputs={"places": LocationDependency(provider=scope.provider_id, primary=True)},
        outputs={"rows": scoped_schema()},
    )
    def findings(places: gpd.GeoDataFrame, parameters: Scale) -> pd.DataFrame:
        calls.append(name)
        return pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": ["row"] * len(places),
                "value": pd.array(
                    [value * parameters.factor for value in places["count"]],
                    dtype="Int64",
                ),
            }
        )

    return PluginDefinition(metrics=[findings], resources={"places": Places})


@pytest.mark.parametrize("empty", [False, True])
def test_revisions_selections_and_parameters_share_table_without_mixing(
    tmp_path: Path, database: Database, *, empty: bool
) -> None:
    path = tmp_path / "results.sqlite3"
    calls: list[str] = []
    retained = []
    scopes = [
        ProviderScope(
            provider_id="blocks",
            code_revision=code,
            origins=[Origin(id="census", revision=origin)],
        )
        for code, origin in (("1", "1"), ("2", "1"), ("2", "2"))
    ]
    for scope in scopes:
        runner = LocalRunner.from_definitions(
            {"test": scoped_plugin(scope, calls)}, result_store_path=path
        )
        for ids, factor in ((["a"], 1), (["a", "b"], 1), (["a"], 2)):
            selected = [] if empty else ids
            result = runner.run(
                "findings",
                inputs={"places": {"provider": "blocks", "ids": selected}},
                parameters={"factor": factor},
            )
            retained.append((result, scope, selected, factor))
    path.touch()  # Only existence is real; the service guard and SQL fake stay active.
    with ResultStore.open(path) as reader:
        for result, scope, ids, factor in retained:
            reopened = reader.read_result(result.metadata.result_ref)
            assert reopened.metadata.outputs["rows"].owner_scope == scope
            assert sorted(reopened.tables["rows"].location_id) == sorted(ids)
            assert reopened.tables["rows"].value.tolist() == [
                factor * scope_value(scope)
            ] * len(ids)
            single = reader.read_table(result.metadata.result_ref, "rows")
            pd.testing.assert_frame_equal(single, reopened.tables["rows"])
    assert len(calls) == (0 if empty else 9)
    assert len({result.metadata.result_ref for result, *_ in retained}) == 9
    # One registration per named output is a storage contract, across revisions.
    with SQLiteStore(path) as store:
        registrations = store.load_registrations()
    assert len([r for r in registrations if r.node_id == "findings"]) == 1
    assert database.rows["stored_results"]


@pytest.mark.parametrize("empty", [False, True])
def test_equal_bare_ids_keep_concrete_metric_scopes(
    tmp_path: Path, database: Database, *, empty: bool
) -> None:
    path = tmp_path / "results.sqlite3"
    calls: list[str] = []
    retained = []
    for provider in ("blocks", "tracts"):
        scope = ProviderScope(provider_id=provider)
        name = f"{provider}_findings"
        runner = LocalRunner.from_definitions(
            {"test": scoped_plugin(scope, calls, name)}, result_store_path=path
        )
        result = runner.run(
            name,
            inputs={"places": {"provider": provider, "ids": [] if empty else ["a"]}},
        )
        retained.append((result, scope))
    path.touch()
    with ResultStore.open(path) as reader:
        for result, scope in retained:
            restored = reader.read_result(result.metadata.result_ref)
            assert restored.metadata.outputs["rows"].owner_scope == scope
            assert restored.tables["rows"].location_id.tolist() == (
                [] if empty else ["a"]
            )
    assert len(calls) == (0 if empty else 2)
    headers = [
        r for r in database.rows["stored_results"] if r["node_id"].endswith("_findings")
    ]
    assert len({r["value_key"] for r in headers}) == 2
