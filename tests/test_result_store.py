"""Read-only retained access uses isolated SQL boundaries and owned results."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DatabaseError

from lyra.contracts.errors import (
    InvalidResultReferenceError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.runtime import provider_acquisition
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore, RunResult
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import PluginDefinition, loading, metric
from lyra.server.response_budget import http_response_budget
from lyra.server.result_rows import RowPageBuilder
from test_support.publication import publish_result
from test_support.table_fixtures import value_frame, value_schema
from tests.result_description_helpers import publish_values
from tests.retained_table_helpers import retained_service
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.parametrize(
    ("dtype", "valid", "damaged"),
    [
        ("Float64", 1.5, float("inf")),
        ("Float64", 1.5, float("-inf")),
        ("Float64", 1.5, "NaN"),
        ("Float64", 1.5, b"12.5"),
        ("Int64", 1, 1.5),
        ("boolean", True, 2),
        ("string", "ok", b"abc"),
    ],
)
def test_damaged_cell_is_an_integrity_error(
    database: Database, tmp_path: Path, dtype: str, valid: object, damaged: object
) -> None:
    path = tmp_path / "results.sqlite"
    schema = pa.DataFrameSchema(
        {
            "value": pa.Column(
                dtype, nullable=True, description="Value", metadata={"unit": None}
            )
        },
        strict=True,
    )
    frame = pd.DataFrame({"value": pd.Series([valid, None], dtype=dtype)})
    _, reference = retained_service(path, schema, frame)
    rows = next(
        rows for name, rows in database.rows.items() if name.startswith("data_")
    )
    with ResultStore.open(path) as store:
        pd.testing.assert_frame_equal(store.read_table(reference, "rows"), frame)
        rows[1]["value"] = damaged
        pages = RowPageBuilder(store, "https://lyra.test", http_response_budget())
        first = pages.build(reference, "rows", limit=1)
        assert first.next_cursor
        with pytest.raises(ResultIntegrityError):
            pages.build(reference, "rows", limit=1, cursor=first.next_cursor)
        with pytest.raises(ResultIntegrityError):
            b"".join(store.stream_table(reference, "rows"))
        with pytest.raises(ResultIntegrityError) as named:
            store.read_table(reference, "rows")
        assert named.value.result_ref == reference
        assert named.value.output_name == "rows"
        with pytest.raises(ResultIntegrityError) as whole:
            store.read_result(reference)
        assert whole.value.result_ref == reference
        assert whole.value.output_name is None


@pytest.mark.parametrize("keys", [(), ("one",), ("one", "two")])
@pytest.mark.parametrize("spatial", [False, True])
def test_read_only_owned_results(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    *,
    spatial: bool,
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    description = publish_values(writer, keys, spatial=spatial)
    reference = publish_result(ResultDescriptions(writer), description)
    path.touch()  # Existence only; SQL remains entirely fake-backed.
    before = deepcopy(database.rows)
    monkeypatch.setattr(SQLiteStore, "initialize", Mock(side_effect=AssertionError))
    monkeypatch.setattr(LocalRunner, "__init__", Mock(side_effect=AssertionError))
    monkeypatch.setattr(GraphExecutor, "execute", Mock(side_effect=AssertionError))
    for module, names in (
        (loading, ("load_plugin_definition", "load_installed_plugin_definition")),
        (
            provider_acquisition,
            ("acquire_provider_input", "acquire_provider_candidates"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(module, name, Mock(side_effect=AssertionError))
    create_engine = Mock(return_value=database.engine)
    monkeypatch.setattr(sa, "create_engine", create_engine)
    with ResultStore.open(path) as reader:
        restored = reader.read_result(reference)
        attached = restored.report
        expected_report = attached.model_copy(deep=True)
        assert attached is restored.report
        assert attached == reader.read_report(restored.metadata.execution_id)
        assert attached.execution_id == restored.metadata.execution_id
        assert attached.result_ref == reference
        assert restored.metadata.result_ref == reference
        assert restored.metadata.store_path == path.resolve()
        assert restored.metadata.parameters == description.parameters
        assert (
            restored.metadata.outputs["a"].owner_scope
            == description.outputs["a"].owner_scope
        )
        assert restored.tables["a"].location_id.tolist() == list(keys)
        assert restored.tables["b"].empty
        assert str(restored.tables["b"].value.dtype) == "Int64"
        if spatial:
            assert restored.tables["b"].crs.to_epsg() == 4326
        original = reader.read_table(reference, "a")
        restored.tables["a"]["value"] = 99
        mutate_report(restored, description.parameters)
        restored.metadata.parameters.clear()
        restored.metadata.outputs["a"].table_schema.columns.clear()
        again = reader.read_result(reference)
        pd.testing.assert_frame_equal(original, again.tables["a"])
        assert again.report == expected_report
        assert again.metadata.parameters == description.parameters
        assert again.metadata.outputs["a"].table_schema.columns
    database.connection.execute.side_effect = AssertionError("Reader is closed")
    assert restored.report is attached
    assert again.report == expected_report
    assert again.tables["b"].empty
    assert database.rows == before
    assert create_engine.call_args.args[0].query == {"mode": "ro", "uri": "true"}
    database.engine.dispose.assert_called_once()
    with pytest.raises(RuntimeError, match="closed"):
        reader.read_result(reference)


def test_missing_path_leaves_no_directories(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "results.sqlite"
    with pytest.raises(FileNotFoundError):
        ResultStore.open(path)
    assert not path.parent.exists()


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("output", [None, "a"])
@pytest.mark.parametrize("reference", ["", "bad", "a" * 31, "A" * 32, "0" * 32])
def test_reference_errors(
    tmp_path: Path,
    output: str | None,
    reference: str,
) -> None:
    path = tmp_path / "results.sqlite"
    path.touch()
    expected = (
        ResultNotFoundError if reference == "0" * 32 else InvalidResultReferenceError
    )
    with ResultStore.open(path) as reader, pytest.raises(expected) as captured:
        reader.read_result(reference) if output is None else reader.read_table(
            reference, output
        )
    assert isinstance(captured.value, ResultAccessError)
    assert captured.value.result_ref == reference
    assert captured.value.output_name == output


@pytest.mark.parametrize(
    "damage", ["header", "rows", "groups", "registration", "description", "completion"]
)
@pytest.mark.parametrize("output", [None, "a"])
def test_required_damage_is_integrity_error(
    database: Database,
    tmp_path: Path,
    damage: str,
    output: str | None,
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    description = publish_values(writer, ("one",))
    reference = publish_result(ResultDescriptions(writer), description)
    path.touch()
    if damage == "header":
        database.rows["stored_results"].clear()
    elif damage == "groups":
        database.rows["stored_partitions"].clear()
    elif damage == "registration":
        database.rows["storage_registrations"].clear()
    elif damage == "description":
        database.rows["final_results"][0]["description"] = "{"
    elif damage == "completion":
        database.rows["stored_results"][0]["output_rows"] = None
    else:
        database.rows[
            writer.layout.tables[description.outputs["a"].registration_key].table.name
        ].clear()
    with (
        ResultStore.open(path) as reader,
        pytest.raises(ResultIntegrityError) as captured,
    ):
        reader.read_result(reference) if output is None else reader.read_table(
            reference, output
        )
    assert captured.value.result_ref == reference
    assert captured.value.output_name == output
    assert captured.value.__cause__ is not None


def test_single_output_ignores_unrelated_values(
    database: Database, tmp_path: Path
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    description = publish_values(writer, ("one",))
    reference = publish_result(ResultDescriptions(writer), description)
    path.touch()
    saved = json.loads(database.rows["final_results"][0]["description"])
    saved["outputs"]["b"]["computations"][0]["value_key"] = "missing"
    database.rows["final_results"][0]["description"] = json.dumps(saved)
    for row in database.rows["storage_registrations"]:
        if row["registration_key"] == description.outputs["b"].registration_key:
            row["description"] = "{"
    with ResultStore.open(path) as reader:
        assert reader.read_table(reference, "a").value.tolist() == [1]
        with pytest.raises(ResultNotFoundError) as captured:
            reader.read_table(reference, "unknown")
        assert captured.value.output_name == "unknown"
        with pytest.raises(ResultIntegrityError):
            reader.read_result(reference)


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("empty", [False, True])
def test_local_metadata_and_scalar_reconstruction(
    tmp_path: Path, *, empty: bool
) -> None:
    declaration = pa.DataFrameSchema(
        {
            name: pa.Column(
                dtype, nullable=True, description=name, metadata={"unit": None}
            )
            for name, dtype in {
                "text": str,
                "integer": "Int64",
                "real": "Float64",
                "flag": "boolean",
            }.items()
        },
        strict=True,
        metadata={"meaning": "Nullable observations"},
    )

    @metric(name="nullable", description="Nullable", outputs={"data": declaration})
    def nullable() -> pd.DataFrame:
        return pd.DataFrame(
            {
                name: pd.array([] if empty else [None, None], dtype=dtype)
                for name, dtype in {
                    "text": "string",
                    "integer": "Int64",
                    "real": "Float64",
                    "flag": "boolean",
                }.items()
            }
        )

    path = tmp_path / "store.sqlite"
    result = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[nullable])},
        result_store_path=path,
    ).run("nullable", inputs={})
    path.touch()
    with ResultStore.open(path) as reader:
        restored = reader.read_result(result.metadata.result_ref)
        described = reader.describe(result.metadata.result_ref)
        assert described.model_dump() == result.metadata.model_dump(
            exclude={"store_path"}
        )
        assert restored.report == result.report
        assert restored.metadata == result.metadata
        assert restored.metadata.outputs["data"].owner_scope is None
        assert restored.metadata.outputs["data"].scope == "unpartitioned"
        assert [str(dtype) for dtype in restored.tables["data"].dtypes] == [
            "string",
            "Int64",
            "Float64",
            "boolean",
        ]
        assert len(restored.tables["data"]) == (0 if empty else 2)


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("reference", ["", "bad", "A" * 32, "0" * 32])
def test_description_reference_errors(tmp_path: Path, reference: str) -> None:
    path = tmp_path / "results.sqlite"
    path.touch()
    expected = (
        ResultNotFoundError if reference == "0" * 32 else InvalidResultReferenceError
    )
    with ResultStore.open(path) as reader, pytest.raises(expected) as captured:
        reader.describe(reference)
    assert captured.value.result_ref == reference
    assert captured.value.output_name is None


@pytest.mark.parametrize(
    "damage",
    [
        "report",
        "execution_id",
        "description",
        "inventory",
        "registration",
        "schema",
        "header",
        "owner",
    ],
)
def test_description_required_relationships(
    database: Database, tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(
        ResultDescriptions(writer), publish_values(writer, ("one",))
    )
    path.touch()
    record = database.rows["final_results"][0]
    if damage == "report":
        record["report"] = None
    elif damage == "execution_id":
        record["execution_id"] = "invalid"
    elif damage == "description":
        record["description"] = "{"
    elif damage == "registration":
        database.rows["storage_registrations"].clear()
    elif damage == "schema":
        registration = database.rows["storage_registrations"][0]
        saved = json.loads(registration["description"])
        saved["table_schema"] = None
        registration["description"] = json.dumps(saved)
    elif damage == "header":
        database.rows["stored_results"].clear()
    else:
        saved = json.loads(record["description"])
        if damage == "inventory":
            del saved["outputs"]["b"]
        else:
            saved["outputs"]["a"]["owner_scope"]["provider_id"] = "different"
        record["description"] = json.dumps(saved)
    with (
        ResultStore.open(path) as reader,
        pytest.raises(ResultIntegrityError) as captured,
    ):
        reader.describe(reference)
    assert captured.value.result_ref == reference
    assert captured.value.output_name is None
    assert captured.value.__cause__ is not None


@pytest.mark.parametrize("fault", ["missing_schema", "sql"])
def test_repeated_read_detects_required_damage_and_closes(
    database: Database,
    tmp_path: Path,
    fault: str,
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(
        ResultDescriptions(writer), publish_values(writer, ("one",))
    )
    path.touch()
    with ResultStore.open(path) as reader:
        frame = reader.read_table(reference, "a")
        if fault == "missing_schema":
            database.rows["storage_registrations"].clear()
        else:
            database.connection.execute.side_effect = DatabaseError(
                "SELECT",
                {},
                RuntimeError("damaged database"),
            )
        with pytest.raises(ResultIntegrityError):
            reader.read_table(reference, "a")
    assert frame.value.tolist() == [1]
    database.engine.dispose.assert_called_once()


@pytest.mark.parametrize("keys", [(), ("one",)])
def test_missing_declared_output_selection(
    database: Database, tmp_path: Path, keys: tuple[str, ...]
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(ResultDescriptions(writer), publish_values(writer, keys))
    path.touch()
    saved = json.loads(database.rows["final_results"][0]["description"])
    del saved["outputs"]["b"]
    database.rows["final_results"][0]["description"] = json.dumps(saved)
    with ResultStore.open(path) as reader:
        assert reader.read_table(reference, "a").location_id.tolist() == list(keys)
        for output in (None, "b"):
            with pytest.raises(ResultIntegrityError) as captured:
                reader.read_result(reference) if output is None else reader.read_table(
                    reference, output
                )
            assert captured.value.result_ref == reference
            assert captured.value.output_name == output
        with pytest.raises(ResultNotFoundError):
            reader.read_table(reference, "undeclared")


@pytest.mark.parametrize("damage", ["selection", "inventory", "duplicate", "empty"])
def test_unpartitioned_empty_output_inventory(
    database: Database, tmp_path: Path, damage: str
) -> None:
    schema = value_schema()

    @metric(
        name="empty", description="Empty tables", outputs={"a": schema, "b": schema}
    )
    def empty() -> dict[str, pd.DataFrame]:
        return {"a": value_frame(), "b": value_frame()}

    path = tmp_path / "empty.sqlite"
    result = LocalRunner.from_definitions(
        {"test": PluginDefinition(metrics=[empty])}, result_store_path=path
    ).run("empty")
    path.touch()
    saved = json.loads(database.rows["final_results"][0]["description"])
    if damage == "selection":
        del saved["outputs"]["b"]
    elif damage == "inventory":
        del saved["output_names"]
    else:
        saved["output_names"] = ["a", "a"] if damage == "duplicate" else []
    database.rows["final_results"][0]["description"] = json.dumps(saved)
    with ResultStore.open(path) as reader, pytest.raises(ResultIntegrityError):
        reader.read_result(result.metadata.result_ref)


def mutate_report(restored: RunResult, parameters: dict) -> None:
    attached = restored.report
    nested = attached.request.parameters["nested"]
    assert isinstance(nested, dict)
    values = nested["list"]
    assert isinstance(values, list)
    values.append(99)
    attached.nodes[0].parameters.clear()
    attached.nodes[0].outputs["a"].table_schema.columns.clear()
    attached.result_ref = "0" * 32
    assert restored.metadata.parameters == parameters
    assert restored.metadata.outputs["a"].table_schema.columns
