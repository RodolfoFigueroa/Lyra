"""SQLite lifecycle and atomic immutable publication shared by value writers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, Literal, Self

import sqlalchemy as sa
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from sqlalchemy import event, exc
from sqlalchemy.dialects.sqlite import insert

from lyra.contracts.provider_scope import DatasetScope, RecordIdentity
from lyra.runtime.sqlite_schema import Layout, Registration

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


_DATASET_SCOPE = TypeAdapter(DatasetScope)


class MissingValueError(LookupError):
    """A valid address has no retained value."""


class DamagedDataError(RuntimeError):
    """Required retained data or relationships cannot be read."""


RowCount = Annotated[int, Field(strict=True, ge=0)]


class Header(BaseModel):
    """Completed value identity and concrete correspondence contexts."""

    model_config = ConfigDict(frozen=True)
    value_key: str
    node_id: str
    output_scope: Literal["location", "unpartitioned"]
    owner_scope: DatasetScope | None = None
    target_scope: DatasetScope | None = None
    output_rows: dict[str, RowCount | dict[str, RowCount]] | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Header:
        """Require exactly the context supported by the derived output scope.

        Returns:
            The validated header.

        Raises:
            ValueError: If scope and owner disagree.
        """
        if (self.output_scope == "location") != (self.owner_scope is not None) or (
            self.output_scope == "unpartitioned" and self.target_scope is not None
        ):
            msg = "Header owner must match output scope"
            raise ValueError(msg)
        if self.output_rows is not None and (
            not self.output_rows
            or any(
                (not isinstance(count, int) or count < 0)
                if self.output_scope == "unpartitioned"
                else (not isinstance(count, dict) or any(n < 0 for n in count.values()))
                for count in self.output_rows.values()
            )
        ):
            msg = "Invalid metric output row counts"
            raise ValueError(msg)
        return self

    @classmethod
    def for_record(cls, key: str, record: RecordIdentity) -> Self:
        """Return the immutable completion header for a qualified record."""
        return cls(
            value_key=key,
            node_id=record.scope.provider_id,
            output_scope="location",
            owner_scope=record.scope,
        )

    def record(self) -> dict:
        """Return native SQL cells with deterministic provider-scope metadata."""
        return {
            "value_key": self.value_key,
            "node_id": self.node_id,
            "output_scope": self.output_scope,
            "owner_scope": _scope_json(self.owner_scope),
            "target_scope": _scope_json(self.target_scope),
            "output_rows": None
            if self.output_rows is None
            else json.dumps(self.output_rows, sort_keys=True),
        }


def _scope_json(scope: DatasetScope | None) -> str | None:
    return None if scope is None else json.dumps(scope.identity_payload, sort_keys=True)


def _configure_connection(connection: sqlite3.Connection, _record: object) -> None:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        msg = "Retained result storage requires SQLite 3.37 or newer"
        raise RuntimeError(msg)
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


class SQLiteStore:
    """Explicitly owned engine; catalog construction never opens the store."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        """Configure an engine without initializing schema or opening connections."""
        self.path = path.absolute()
        self.read_only = read_only
        self.layout = Layout()
        database = self.path.as_uri() if read_only else str(self.path)
        self.engine = sa.create_engine(
            sa.URL.create(
                "sqlite+pysqlite",
                database=database,
                query={"mode": "ro", "uri": "true"} if read_only else {},
            ),
            connect_args={"timeout": 30},
        )
        event.listen(self.engine, "connect", _configure_connection)

    def close(self) -> None:
        """Dispose pooled connections, including after failed operations."""
        self.engine.dispose()

    def __enter__(self) -> Self:
        """Return this explicitly owned store."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the engine on normal and exceptional exit."""
        self.close()

    @contextmanager
    def transaction(self) -> Generator[sa.Connection, None, None]:
        """Serialize writers before checking keys and commit all rows together.

        Yields:
            One connection for bulk statements across any number of values.

        Raises:
            PermissionError: If writes are requested on a read-only store.
        """
        if self.read_only:
            msg = "Store is read-only"
            raise PermissionError(msg)
        with self.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection

    def initialize(self, registrations: tuple[Registration, ...] = ()) -> None:
        """Initialize runtime tables and retain schemas once under a writer lock.

        Raises:
            PermissionError: If initialization is requested on a read-only store.
        """
        if self.read_only:
            msg = "Store is read-only"
            raise PermissionError(msg)
        previous = self.layout
        self.layout = Layout()
        try:
            self._initialize(registrations)
        except BaseException:
            self.layout = previous
            raise

    def _initialize(self, registrations: tuple[Registration, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            self.layout.metadata.create_all(connection)
            self._load_registrations(connection)
            for registration in registrations:
                self.layout.register(registration)
                statement = sa.select(
                    self.layout.registrations.c.registration_key
                ).where(
                    self.layout.registrations.c.registration_key == registration.key
                )
                if connection.execute(statement).scalar_one_or_none() is None:
                    connection.execute(
                        self.layout.registrations.insert(),
                        {
                            "registration_key": registration.key,
                            "description": registration.model_dump_json(),
                        },
                    )
            self.layout.metadata.create_all(connection)

    def _load_registrations(
        self, connection: sa.Connection, keys: tuple[str, ...] | None = None
    ) -> None:
        query = sa.select(self.layout.registrations)
        if keys is not None:
            query = query.where(self.layout.registrations.c.registration_key.in_(keys))
        found = set()
        for row in connection.execute(query).mappings():
            found.add(row["registration_key"])
            registration = Registration.model_validate_json(row["description"])
            if registration.key != row["registration_key"]:
                msg = "Damaged schema registration address"
                raise DamagedDataError(msg)
            self.layout.register(registration)
        if keys is not None and found != set(keys):
            msg = "Missing required schema registration"
            raise DamagedDataError(msg)

    def read_registration(self, connection: sa.Connection, key: str) -> Registration:
        """Read a required provider schema in the current transaction.

        Returns:
            The retained logical registration.
        """
        self._load_registrations(connection, (key,))
        return self.layout.descriptions[key]

    def load_registrations(
        self, keys: tuple[str, ...] | None = None
    ) -> tuple[Registration, ...]:
        """Read logical schemas without plugins, directory creation, or DDL.

        Returns:
            All retained descriptions and their locally constructed table layouts.

        Raises:
            DamagedDataError: If required schema metadata is missing or invalid.
        """
        try:
            with self.engine.connect() as connection:
                self._load_registrations(connection, keys)
        except (exc.DatabaseError, ValueError) as error:
            msg = "Cannot read retained schema registrations"
            raise DamagedDataError(msg) from error
        return tuple(self.layout.descriptions.values())

    def has_intervention_validation(self, key: str) -> bool:
        """Return whether this base scope and intervention passed validation."""
        column = self.layout.intervention_validations.c.validation_key
        with self.engine.connect() as connection:
            query = sa.select(column).where(column == key)
            return connection.execute(query).scalar_one_or_none() is not None

    def publish_intervention_validation(self, key: str) -> None:
        """Commit a successful check, allowing concurrent identical publications."""
        table = self.layout.intervention_validations
        statement = insert(table).on_conflict_do_nothing(
            index_elements=[table.c.validation_key]
        )
        with self.transaction() as connection:
            connection.execute(statement, {"validation_key": key})

    def header(
        self, connection: sa.Connection, key: str, *, required: bool = False
    ) -> Header:
        """Read completion without recursively inspecting the value graph.

        Returns:
            A completed header.

        Raises:
            MissingValueError: If an optional value is absent.
            DamagedDataError: If a required reference or stored header is damaged.
        """
        try:
            row = (
                connection.execute(
                    sa.select(self.layout.results).where(
                        self.layout.results.c.value_key == key
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                return self._decode_header(dict(row))
        except (exc.DatabaseError, ValidationError, ValueError) as error:
            msg = f"Damaged value header: {key}"
            raise DamagedDataError(msg) from error
        if required:
            raise DamagedDataError(key)
        raise MissingValueError(key)

    @staticmethod
    def _decode_header(data: dict) -> Header:
        for name in ("owner_scope", "target_scope"):
            if data[name] is not None:
                data[name] = _DATASET_SCOPE.validate_json(data[name])
        if data.get("output_rows") is not None:
            data["output_rows"] = json.loads(data["output_rows"])
        return Header.model_validate(data)

    @contextmanager
    def publication(
        self, header: Header, partitions: tuple[str, ...] = ()
    ) -> Generator[sa.Connection | None, None, None]:
        """Publish one complete value; yield None for an already retained key.

        Value-specific writers must insert every validated output using the yielded
        connection. Exceptions roll back the header, partitions, and all data.

        Yields:
            The transaction connection, or None when the key already exists.

        Raises:
            ValueError: If an unpartitioned value supplies partitions.
        """
        if header.output_scope == "unpartitioned" and partitions:
            msg = "Unpartitioned values cannot own partitions"
            raise ValueError(msg)
        with self.transaction() as connection:
            try:
                self.header(connection, header.value_key)
            except MissingValueError:
                connection.execute(self.layout.results.insert(), header.record())
                if partitions:
                    connection.execute(
                        self.layout.partitions.insert(),
                        [
                            {"value_key": header.value_key, "location_id": location}
                            for location in partitions
                        ],
                    )
                yield connection
            else:
                yield None
