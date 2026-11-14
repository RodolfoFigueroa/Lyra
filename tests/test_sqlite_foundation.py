from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pandera.pandas as pa
import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.sqlite_schema import Layout, Registration, StoredColumn
from lyra.runtime.sqlite_store import (
    DamagedDataError,
    Header,
    MissingValueError,
    SQLiteStore,
)
from lyra.sdk.storage_schema import describe
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from pathlib import Path


def registration(kind: str = "metric", scope: str = "location") -> Registration:
    names = ["location_id", "row_key"] if scope == "location" else []
    if kind == "provider":
        names = ["location_id"]
    if kind == "operation":
        names = ["source_location_id", "location_id"]
    return Registration.model_validate(
        {
            "node_id": "producer",
            "output_name": "output",
            "kind": kind,
            "output_scope": scope,
            "columns": [
                {"name": name, "dtype": "string", "nullable": False} for name in names
            ]
            + [{"name": "enabled", "dtype": "boolean", "nullable": True}],
        }
    )


@pytest.mark.parametrize(
    ("kind", "scope", "primary"),
    [
        ("provider", "location", ["value_key", "location_id"]),
        ("operation", "location", ["value_key", "source_location_id", "location_id"]),
        ("metric", "location", ["value_key", "location_id", "row_key"]),
        ("metric", "unpartitioned", ["value_key", "row_index"]),
    ],
)
def test_offline_constraints(kind: str, scope: str, primary: list[str]) -> None:
    layout = Layout()
    definition = registration(kind, scope)
    data = layout.register(definition)
    assert layout.register(definition) is data
    assert list(data.table.primary_key.columns.keys()) == primary
    assert isinstance(data.table.c.enabled.type, sa.Boolean)
    assert data.table.c.enabled.nullable
    assert all(not column.nullable for column in data.table.primary_key)
    constraints = list(data.table.foreign_key_constraints)
    assert len(constraints) == 1
    expected = "stored_partitions" if scope == "location" else "stored_results"
    assert constraints[0].referred_table.name == expected
    if kind == "operation":
        assert list(constraints[0].columns.keys()) == [
            "value_key",
            "source_location_id",
        ]
    for table in layout.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))
        assert "CREATE TABLE" in ddl
    if scope == "location":
        assert data.table.indexes
    statement = data.table.insert().values(
        {column.name: None for column in data.table.columns}
    )
    assert "INSERT INTO" in str(statement.compile(dialect=sqlite.dialect()))


def test_registration_metadata_and_collision() -> None:
    declaration = pa.DataFrameSchema(
        {
            "value_key": pa.Column(
                "boolean",
                nullable=True,
                description="Enabled",
                metadata={"unit": "flag"},
            ),
            "row_index": pa.Column("Int64", nullable=True),
        },
        strict=True,
    )
    definition = Registration.from_description(
        "metric", "result", describe(declaration), output_scope="unpartitioned"
    )
    restored = Registration.model_validate_json(definition.model_dump_json())
    assert restored == definition
    assert restored.columns[0].description == "Enabled"
    assert restored.columns[0].unit == "flag"
    layout = Layout()
    data = layout.register(restored)
    assert data.value_column not in declaration.columns
    assert data.row_column not in declaration.columns
    assert set(declaration.columns) <= set(data.table.c.keys())
    with pytest.raises(ValueError, match="conflicts"):
        layout.register(restored.model_copy(update={"columns": ()}))


def test_invalid_structural_layouts() -> None:
    definition = registration()
    layout = Layout()
    with pytest.raises(ValueError, match="structural"):
        layout.register(definition.model_copy(update={"columns": ()}))
    bad = StoredColumn(name="location_id", dtype="string", nullable=True)
    with pytest.raises(ValueError, match="structural"):
        layout.register(
            definition.model_copy(update={"columns": (bad, *definition.columns[1:])})
        )
    with pytest.raises(ValueError, match="owner"):
        Header(value_key="key", node_id="node", output_scope="location")


def test_publication_rollback_and_immutability(
    database: Database, tmp_path: Path
) -> None:
    header = Header(
        value_key="key",
        node_id="metric",
        output_scope="location",
        owner_scope=ProviderScope(provider_id="places"),
    )
    with SQLiteStore(tmp_path / "store.sqlite") as store:
        store.initialize((registration(),))
        data = store.layout.tables[registration().key]

        def fail_publication() -> None:
            with store.publication(header, ("a",)) as connection:
                assert connection is not None
                connection.execute(
                    data.table.insert(),
                    {
                        "value_key": "key",
                        "location_id": "a",
                        "row_key": "r",
                        "enabled": True,
                    },
                )
                msg = "write failure"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="write failure"):
            fail_publication()
        assert not database.rows.get("stored_results")
        assert not database.rows.get(data.table.name)
        with store.publication(header, ("a",)) as connection:
            assert connection is not None
            connection.execute(
                data.table.insert(),
                {
                    "value_key": "key",
                    "location_id": "a",
                    "row_key": "r",
                    "enabled": True,
                },
            )
        original = deepcopy(database.rows)
        with store.publication(header, ("different",)) as connection:
            assert connection is None
        assert database.rows == original
        assert store.header(database.connection, "key") == header
        database.connection.exec_driver_sql.assert_called_with("BEGIN IMMEDIATE")
    database.engine.dispose.assert_called_once()
    assert database.connection.__exit__.called


def test_commit_failure_and_missing_relationship(
    database: Database, tmp_path: Path
) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite")
    header = Header(value_key="key", node_id="metric", output_scope="unpartitioned")
    database.fail_commit = True
    with pytest.raises(RuntimeError, match="commit failure"), store.publication(header):
        pass
    assert not database.rows
    with pytest.raises(MissingValueError):
        store.header(database.connection, "key")
    with pytest.raises(DamagedDataError):
        store.header(database.connection, "key", required=True)
    with (
        pytest.raises(ValueError, match="partitions"),
        store.publication(header, ("a",)),
    ):
        pass
    database.rows["stored_results"] = [{**header.record(), "owner_scope": "broken"}]
    with pytest.raises(DamagedDataError):
        store.header(database.connection, "key")


def test_registration_reopening_and_read_only(
    database: Database, tmp_path: Path
) -> None:
    path = tmp_path / "missing" / "data #?.sqlite"
    definition = registration()
    with SQLiteStore(path, read_only=True) as reader:
        assert isinstance(sa.create_engine, MagicMock)
        url = sa.create_engine.call_args.args[0]
        assert url.query == {"mode": "ro", "uri": "true"}
        arguments, options = sqlite.dialect().create_connect_args(url)
        assert options["uri"] is True
        assert arguments[0].endswith("?mode=ro")
        assert "%23%3F" in url.database
        assert not path.parent.exists()
        with pytest.raises(PermissionError):
            reader.initialize()
        with pytest.raises(PermissionError), reader.transaction():
            pass
    with SQLiteStore(path) as writer:
        writer.initialize((definition,))
        writer.initialize((definition,))
    assert len(database.rows["storage_registrations"]) == 1
    with SQLiteStore(path, read_only=True) as reader:
        assert reader.load_registrations() == (definition,)
        assert reader.layout.tables[definition.key].table.c.enabled.nullable
    database.rows["storage_registrations"][0]["description"] = "{}"
    with SQLiteStore(path, read_only=True) as reader, pytest.raises(DamagedDataError):
        reader.load_registrations()


def test_connection_setup_and_failure_cleanup(
    database: Database, tmp_path: Path
) -> None:
    with SQLiteStore(tmp_path / "store.sqlite"):
        assert isinstance(event.listen, MagicMock)
        callback = event.listen.call_args.args[2]
        raw = MagicMock()
        callback(raw, None)
        raw.cursor.return_value.execute.assert_called_once_with(
            "PRAGMA foreign_keys=ON"
        )
        raw.cursor.return_value.close.assert_called_once()
        raw.cursor.return_value.execute.side_effect = RuntimeError("pragma failure")
        with pytest.raises(RuntimeError, match="pragma failure"):
            callback(raw, None)
        assert raw.cursor.return_value.close.call_count == 2
    database.connection.exec_driver_sql.side_effect = RuntimeError("lock failure")
    with (
        SQLiteStore(tmp_path / "store.sqlite") as store,
        pytest.raises(RuntimeError, match="lock failure"),
        store.transaction(),
    ):
        pass
    assert database.connection.__exit__.called


def test_failed_registration_does_not_poison_retry(
    database: Database, tmp_path: Path
) -> None:
    with SQLiteStore(tmp_path / "store.sqlite") as store:
        database.fail_commit = True
        with pytest.raises(RuntimeError, match="commit failure"):
            store.initialize((registration(),))
        assert not store.layout.descriptions
        assert not database.rows
        database.fail_commit = False
        replacement = registration().model_copy(update={"crs": "EPSG:4326"})
        store.initialize((replacement,))
        assert store.load_registrations() == (replacement,)


__all__ = ["database"]
