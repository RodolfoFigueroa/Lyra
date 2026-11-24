"""Bounded SQL traversal of retained metric rows, without producer dependencies."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlalchemy as sa

from lyra.data.relational import frame_from_rows
from lyra.runtime.sqlite_store import DamagedDataError

if TYPE_CHECKING:
    from collections.abc import Generator

    import pandas as pd

    from lyra.contracts.provider_scope import DatasetScope
    from lyra.data.relational import Cell
    from lyra.runtime.sqlite_schema import DataTable, Registration
    from lyra.runtime.sqlite_store import Header, SQLiteStore


def _read(connection: sa.Connection, statement: sa.Select) -> list[sa.RowMapping]:
    result = connection.execute(statement)
    try:
        return list(result.mappings())
    finally:
        result.close()


@dataclass(frozen=True)
class ComputationRows:
    """SQL selection qualified by a validated computation key and its groups."""

    registration: Registration
    data: DataTable
    value_key: str
    groups: tuple[str, ...] | None

    @property
    def keys(self) -> tuple[sa.Column, ...]:
        """Stable private ordering columns, independent of visible values."""
        columns = self.data.table.c
        return (
            (columns.location_id, columns.row_key)
            if self.registration.output_scope == "location"
            else (columns[self.data.row_column],)
        )

    def select(self, *columns: sa.ColumnElement) -> sa.Select:
        """Return a projection constrained to this computation and selected groups."""
        table = self.data.table
        query = sa.select(*columns).where(
            table.c[self.data.value_column] == self.value_key
        )
        if self.groups is not None:
            query = query.where(table.c.location_id.in_(self.groups))
        return query

    def validate(self, connection: sa.Connection, counts: int | dict[str, int]) -> None:
        """Check completion counts and identity uniqueness in SQL.

        Raises:
            DamagedDataError: If selected rows disagree with retained completion.
        """
        query = self.select(
            sa.func.count().label("rows"),
            sa.func.count(self.keys[-1].distinct()).label("identities"),
        )
        expected = (
            {None: counts}
            if isinstance(counts, int)
            else {
                group: count
                for group, count in counts.items()
                if self.groups is None or group in self.groups
            }
        )
        if isinstance(counts, dict):
            owner = self.data.table.c.location_id
            query = query.add_columns(owner.label(owner.key)).group_by(owner)
        observed = {}
        for row in _read(connection, query):
            group = row["location_id"] if isinstance(counts, dict) else None
            observed[group] = row["rows"]
            if row["identities"] != row["rows"]:
                msg = "Invalid metric row identities"
                raise DamagedDataError(msg)
        if observed.keys() - expected.keys() or any(
            observed.get(group, 0) != count for group, count in expected.items()
        ):
            msg = "Incomplete metric output rows"
            raise DamagedDataError(msg)


@dataclass
class ComputationReader:
    """Validate producing schemas once per selected output and transaction."""

    store: SQLiteStore
    connection: sa.Connection
    registration: Registration
    _tables: dict[str, DataTable] = field(default_factory=dict, init=False)

    def table(self, header: Header) -> DataTable:
        """Resolve the physical table shared by the producing and selected declarations.

        Returns:
            The compatible table from the retained computation's inventory.

        Raises:
            DamagedDataError: If the producing schema is missing or incompatible.
        """
        key = (header.output_registrations or {}).get(self.registration.output_name)
        if key is None:
            msg = "Missing metric output registration"
            raise DamagedDataError(msg)
        if key not in self._tables:
            if self.store.layout.descriptions.get(key) is not self.registration:
                produced = self.store.read_registration(self.connection, key)
                if produced.table_key != self.registration.table_key:
                    msg = "Retained metric output registrations differ"
                    raise DamagedDataError(msg)
            self._tables[key] = self.store.layout.tables[key]
        return self._tables[key]

    def prepare(
        self, header: Header, groups: tuple[str, ...] | None
    ) -> ComputationRows:
        """Validate required completion without materializing output values.

        Returns:
            A bounded SQL reader for exactly the retained computation and groups.

        Raises:
            DamagedDataError: If the required registration or partitions disagree.
        """
        registration = self.registration
        connection = self.connection
        if (
            registration.kind != "metric"
            or registration.node_id != header.node_id
            or registration.output_scope != header.output_scope
            or (header.output_scope == "unpartitioned" and groups is not None)
        ):
            msg = "Incomplete metric output descriptions or scope"
            raise DamagedDataError(msg)
        if (
            header.output_rows is None
            or registration.output_name not in header.output_rows
        ):
            msg = "Incomplete metric output descriptions"
            raise DamagedDataError(msg)
        data = self.table(header)
        counts = header.output_rows[registration.output_name]
        if isinstance(counts, dict):
            partitions = self.store.layout.partitions
            retained = {
                row["location_id"]
                for row in _read(
                    connection,
                    sa.select(partitions.c.location_id).where(
                        partitions.c.value_key == header.value_key
                    ),
                )
            }
            if set(counts) != retained or (
                groups is not None and not set(groups) <= retained
            ):
                msg = "Incomplete or missing metric partitions"
                raise DamagedDataError(msg)
        selection = ComputationRows(registration, data, header.value_key, groups)
        selection.validate(connection, counts)
        return selection


@dataclass(frozen=True)
class OutputSelection:
    """One named output using a caller-owned connection and retained schema."""

    connection: sa.Connection
    registration: Registration
    owner_scope: DatasetScope | None
    computations: tuple[ComputationRows, ...]

    def batches(
        self, batch_size: int = 1024
    ) -> Generator[tuple[tuple[Cell, ...], ...]]:
        """Yield ordered cells in bounded batches from each computation's cursor.

        Raises:
            ValueError: If the batch size is not a positive integer.
        """
        if type(batch_size) is not int or batch_size < 1:
            msg = "Batch size must be a positive integer"
            raise ValueError(msg)
        for computation in self.computations:
            columns = [
                computation.data.table.c[col.name].label(col.name)
                for col in self.registration.columns
            ]
            query = computation.select(*columns).order_by(*computation.keys)
            with closing(self.connection.execute(query)) as cursor:
                for rows in cursor.mappings().partitions(batch_size):
                    yield tuple(
                        tuple(row[column.name] for column in self.registration.columns)
                        for row in rows
                    )

    def frame(self) -> pd.DataFrame:
        """Return a detached native frame using retained logical types and CRS."""
        with closing(self.batches()) as batches:
            return frame_from_rows(
                (row for batch in batches for row in batch),
                self.registration.storage_description(),
            )
