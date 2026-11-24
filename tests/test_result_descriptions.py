"""Final selections are durable independently of jobs and cache reuse."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from rq.job import Job
from sqlalchemy.sql.dml import Insert

from lyra.contracts.reports import ExecutionReport
from lyra.contracts.results import ResultDescription as PublicResultDescription
from lyra.runtime import provider_acquisition
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescriptions,
)
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from lyra.sdk import loading
from test_support.publication import publication_report, publish_result
from tests.result_description_helpers import (
    location_descriptions,
    publish_values,
    retain_descriptions,
)
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


def _forbid_value_reads(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    forbidden = Mock(side_effect=AssertionError("No execution or value reads"))
    for target, names in (
        (Job, ("fetch",)),
        (MetricTables, ("read_connection",)),
        (ExecutionReport, ("model_validate_json",)),
        (SQLiteStore, ("initialize",)),
        (loading, ("load_plugin_definition", "load_installed_plugin_definition")),
        (
            provider_acquisition,
            ("acquire_provider_input", "acquire_provider_candidates"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    execute = database.execute

    def metadata_only(statement: object, parameters: object = None) -> object:
        assert isinstance(statement, sa.sql.Select)
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        assert source.name in {
            "final_results",
            "storage_registrations",
            "stored_results",
            "input_selections",
        }
        if source.name == "final_results":
            # Presence predicates are allowed; transferring the body is not.
            assert "report" not in statement.selected_columns
        return execute(statement, parameters)

    database.connection.execute.side_effect = metadata_only


@pytest.mark.parametrize("keys", [(), ("one",), ("one", "two")])
@pytest.mark.parametrize(
    "shape", [(False, False), (False, True), (True, False), (True, True)]
)
def test_complete_description_without_report_or_values(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    shape: tuple[bool, bool],
) -> None:
    spatial, with_inputs = shape
    path = tmp_path / "values.sqlite"
    writer = SQLiteStore(path)
    retained = publish_values(writer, keys, spatial=spatial)
    if with_inputs:
        retained = retained.model_copy(
            update={"location_inputs": retain_descriptions(writer)}
        )
    reference = publish_result(ResultDescriptions(writer), retained)
    result = ResultStore(writer).read_result(reference)
    expected_outputs = result.metadata.outputs
    assert result.metadata.location_inputs == (
        location_descriptions() if with_inputs else {}
    )
    assert set(result.tables) == {"a", "b"}
    assert isinstance(result.report, ExecutionReport)
    job_id = database.rows["final_results"][0]["job_id"]
    # Descriptions check association and presence, not the full report body.
    database.rows["final_results"][0]["report"] = "{"
    writer.close()
    path.touch()
    before = deepcopy(database.rows)
    _forbid_value_reads(database, monkeypatch)
    with ResultStore.open(path) as reader:
        description = reader.describe_result(reference)
        assert isinstance(description, PublicResultDescription)
        assert description.job_id == reference
        assert description.job_id == job_id
        assert description.metric_name == retained.metric_name
        assert description.parameters == retained.parameters
        assert description.outputs == expected_outputs
        assert description.location_inputs == (
            location_descriptions() if with_inputs else {}
        )
        assert set(description.model_dump()) == {
            "job_id",
            "metric_name",
            "parameters",
            "outputs",
            "location_inputs",
        }
        for output in description.outputs.values():
            assert set(output.model_dump()) == {"scope", "table_schema", "owner_scope"}
        description.parameters.clear()
        description.outputs["a"].table_schema.columns.clear()
        description.outputs["b"].owner_scope = None
        if with_inputs:
            description.location_inputs["blocks"].table_schema.columns[-1].parameters[
                "crs"
            ] = "EPSG:4326"
            description.location_inputs["blocks"].table_schema.columns.clear()
            description.location_inputs["nearby_trees"].source_scope = None
            description.location_inputs["reference_blocks"].availability = "available"
            description.location_inputs.pop("known_empty")
        again = reader.describe(reference)
        assert again.parameters == retained.parameters
        assert again.outputs == expected_outputs
        assert again.location_inputs == (location_descriptions() if with_inputs else {})
        assert reader.describe_result(reference) == again
    assert again.outputs == expected_outputs
    assert again.location_inputs == (location_descriptions() if with_inputs else {})
    with pytest.raises(RuntimeError, match="closed"):
        reader.describe_result(reference)
    with SQLiteStore(path, read_only=True) as store:
        assert ResultDescriptions(store).describe(reference) == again
    assert database.rows == before


@pytest.mark.parametrize("keys", [(), ("one",), ("one", "two")])
@pytest.mark.parametrize("spatial", [False, True])
def test_reopen_assembly(
    database: Database, tmp_path: Path, keys: tuple[str, ...], *, spatial: bool
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, keys, spatial=spatial)
    values = deepcopy(database.rows)
    results = ResultDescriptions(store)
    first = publish_result(results, description)
    second = publish_result(results, description)
    assert first != second
    assert {
        name: rows for name, rows in database.rows.items() if name != "final_results"
    } == values
    reopened = ResultDescriptions(SQLiteStore(store.path))
    restored, frames = reopened.read(first)
    assert restored == description
    assert frames["a"].location_id.tolist() == list(keys)
    assert frames["b"].empty
    if spatial:
        assert frames["a"].crs.to_epsg() == 4326
        assert frames["b"].crs.to_epsg() == 4326
    assert str(frames["b"].value.dtype) == "Int64"
    restored.parameters.clear()
    restored.outputs.clear()
    frames["a"]["value"] = 99
    again, original = reopened.read(first)
    assert again == description
    assert original["a"].value.tolist() == [1] * len(keys)
    assert reopened.read(second)[0] == description


@pytest.mark.parametrize(
    "failure", ["insert", "commit", "missing", "group", "input", "partition", "rows"]
)
def test_failed_description_keeps_values(
    database: Database, tmp_path: Path, failure: str
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, ("one",))
    description = description.model_copy(
        update={"location_inputs": retain_descriptions(store)}
    )
    valid = description.model_copy(deep=True)
    writer = ResultDescriptions(store)
    report = publication_report(writer, valid)
    undamaged = deepcopy(database.rows)
    if failure == "partition":
        database.rows["stored_partitions"].clear()
    elif failure == "rows":
        data = store.layout.tables[description.outputs["a"].registration_key]
        database.rows[data.table.name].clear()
    values = deepcopy({name: rows for name, rows in database.rows.items() if rows})
    if failure == "input":
        description = description.model_copy(
            update={"input_computations": (ComputationSelection(value_key="absent"),)}
        )
    if failure in {"missing", "group"}:
        description.outputs["b"] = description.outputs["b"].model_copy(
            update={
                "computations": (
                    ComputationSelection(
                        value_key="absent" if failure == "missing" else "one",
                        groups=("absent",),
                    ),
                )
            }
        )
    execute = database.execute

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == "final_results":
            if failure == "insert":
                msg = "publication failure"
                raise RuntimeError(msg)
            database.fail_commit = failure == "commit"
        return execute(statement, parameters)

    database.connection.execute.side_effect = inject
    with pytest.raises((RuntimeError, ValueError, DamagedDataError)):
        writer.publish(description, report)
    assert not database.rows.get("final_results")
    assert len(database.rows["stored_results"]) == 1
    assert {name: rows for name, rows in database.rows.items() if rows} == values
    assert not database.in_transaction
    database.connection.execute.side_effect = execute
    database.fail_commit = False
    if failure in {"partition", "rows"}:
        database.rows = undamaged
    reference = publish_result(writer, valid)
    assert writer.describe(reference).location_inputs == location_descriptions()


@pytest.mark.parametrize("damage", ["missing", "extra"])
def test_incomplete_description_is_not_published(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, ())
    writer = ResultDescriptions(store)
    report = publication_report(writer, description)
    if damage == "missing":
        del description.outputs["b"]
    else:
        description.outputs["extra"] = description.outputs["b"]
    with pytest.raises(DamagedDataError):
        writer.publish(description, report)
    assert not database.rows.get("final_results")


@pytest.mark.parametrize(
    ("first", "second_key", "second"),
    [
        (("one",), "one", ("one",)),
        (None, "one", ("one",)),
        (("empty",), "two", ("empty",)),
    ],
)
def test_overlapping_output_selections_fail_publication(
    database: Database,
    tmp_path: Path,
    first: tuple[str, ...] | None,
    second_key: str,
    second: tuple[str, ...],
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, ("one", "two"))
    description.outputs["a"] = description.outputs["a"].model_copy(
        update={
            "computations": (
                ComputationSelection(value_key="one", groups=first),
                ComputationSelection(value_key=second_key, groups=second),
            )
        }
    )
    with pytest.raises(DamagedDataError, match="Overlapping"):
        publish_result(ResultDescriptions(store), description)
    assert not database.rows.get("final_results")


@pytest.mark.parametrize("groups", [None, (), ("empty",)])
def test_empty_selected_groups_keep_scope_and_schema(
    database: Database, tmp_path: Path, groups: tuple[str, ...] | None
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    description = publish_values(store, ("one",), spatial=True)
    for name, output in description.outputs.items():
        description.outputs[name] = output.model_copy(
            update={
                "computations": (ComputationSelection(value_key="one", groups=groups),),
            }
        )
    descriptions = ResultDescriptions(store)
    reference = publish_result(descriptions, description)
    before = deepcopy(database.rows)
    for name in ("a", "b"):
        with descriptions.select(reference, name) as selection:
            assert selection.owner_scope == description.outputs[name].owner_scope
            batches = list(selection.batches(1))
            frame = selection.frame()
            assert sum(len(batch) for batch in batches) == (
                1 if groups is None and name == "a" else 0
            )
            assert str(frame.value.dtype) == "Int64"
            assert frame.crs.to_epsg() == 4326
    assert database.rows == before


@pytest.mark.parametrize("keys", [(), ("one",), ("one", "two")])
def test_publication_projection_is_detached(
    database: Database, tmp_path: Path, keys: tuple[str, ...]
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, keys, spatial=True)
    description = description.model_copy(
        update={"location_inputs": retain_descriptions(store)}
    )
    writer = ResultDescriptions(store)
    reference = publish_result(writer, description)
    projection = writer.metadata(description, reference)
    assert not database.in_transaction
    expected = writer.describe(reference)
    assert projection == expected
    before = deepcopy(database.rows)
    projection.parameters.clear()
    projection.outputs["a"].table_schema.columns.clear()
    projection.outputs["b"].owner_scope = None
    projection.location_inputs["blocks"].table_schema.columns.clear()
    projection.location_inputs["nearby_trees"].source_scope = None
    projection.location_inputs.pop("known_empty")
    assert writer.describe(reference) == expected
    assert database.rows == before
    # A later read must inspect persisted inputs afresh after publication.
    database.rows["input_selections"].clear()
    with pytest.raises(DamagedDataError):
        writer.describe(reference)
