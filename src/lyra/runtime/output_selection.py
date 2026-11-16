"""Bounded SQL traversal of retained metric rows, without producer dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa

from lyra.data.relational import frame_from_rows
from lyra.runtime.sqlite_store import DamagedDataError

if TYPE_CHECKING:
    from collections.abc import Generator

    import pandas as pd

    from lyra.contracts.provider_scope import ProviderScope
    from lyra.data.relational import Cell
    from lyra.runtime.sqlite_schema import DataTable, Registration
    from lyra.runtime.sqlite_store import Header, SQLiteStore


@dataclass(frozen=True)
class RowAddress:
    """Private traversal position within one retained output selection."""

    computation: int
    key: tuple[str | int, ...]


@dataclass(frozen=True)
class RowBatch:
    """Declared SQL cells and separate private positions for each stored row."""

    rows: tuple[tuple[Cell, ...], ...]
    addresses: tuple[RowAddress, ...]


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

    def statement(
        self, limit: int, after: tuple[str | int, ...] | None = None
    ) -> sa.Select:
        """Select a bounded ordered batch after an optional private row address.

        Returns:
            A SQL query that never deduplicates visible row values.
        """
        query = self.select(*self.data.table.c).order_by(*self.keys).limit(limit)
        if after is not None:
            query = query.where(
                sa.or_(
                    *(
                        sa.and_(
                            *(self.keys[j] == after[j] for j in range(i)),
                            column > after[i],
                        )
                        for i, column in enumerate(self.keys)
                    )
                )
            )
        return query

    def validate(self, connection: sa.Connection, counts: int | dict[str, int]) -> None:
        """Check completion, identity uniqueness and required cells in SQL.

        Raises:
            DamagedDataError: If selected rows disagree with retained completion.
        """
        required = [
            self.data.table.c[column.name]
            for column in self.registration.columns
            if not column.nullable
        ]
        query = self.select(
            sa.func.count().label("rows"),
            sa.func.count(self.keys[-1].distinct()).label("identities"),
            *(
                sa.func.count(column).label(f"required_{i}")
                for i, column in enumerate(required)
            ),
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
            query = query.add_columns(self.data.table.c.location_id).group_by(
                self.data.table.c.location_id
            )
        observed = {}
        for row in _read(connection, query):
            group = row["location_id"] if isinstance(counts, dict) else None
            observed[group] = row["rows"]
            if row["identities"] != row["rows"] or any(
                row[f"required_{i}"] != row["rows"] for i in range(len(required))
            ):
                msg = "Invalid metric row identities or required cells"
                raise DamagedDataError(msg)
        if observed.keys() - expected.keys() or any(
            observed.get(group, 0) != count for group, count in expected.items()
        ):
            msg = "Incomplete metric output rows"
            raise DamagedDataError(msg)


def prepare_computation(
    store: SQLiteStore,
    connection: sa.Connection,
    registration: Registration,
    header: Header,
    groups: tuple[str, ...] | None,
) -> ComputationRows:
    """Validate required completion without materializing output values.

    Returns:
        A bounded SQL reader for exactly the retained computation and groups.

    Raises:
        DamagedDataError: If the required registration or partitions disagree.
    """
    if (
        registration.kind != "metric"
        or registration.node_id != header.node_id
        or registration.output_scope != header.output_scope
        or (header.output_scope == "unpartitioned" and groups is not None)
    ):
        msg = "Incomplete metric output descriptions or scope"
        raise DamagedDataError(msg)
    if header.output_rows is None or registration.output_name not in header.output_rows:
        msg = "Incomplete metric output descriptions"
        raise DamagedDataError(msg)
    counts = header.output_rows[registration.output_name]
    if isinstance(counts, dict):
        partitions = store.layout.partitions
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
    selection = ComputationRows(
        registration, store.layout.tables[registration.key], header.value_key, groups
    )
    selection.validate(connection, counts)
    return selection


@dataclass(frozen=True)
class OutputSelection:
    """One named output using a caller-owned connection and retained schema."""

    connection: sa.Connection
    registration: Registration
    owner_scope: ProviderScope | None
    computations: tuple[ComputationRows, ...]

    def batches(
        self,
        batch_size: int = 1024,
        *,
        after: RowAddress | None = None,
        limit: int | None = None,
    ) -> Generator[RowBatch]:
        """Yield bounded cells and private positions, closing each SQL cursor.

        The caller owns the connection and must keep its context open throughout
        traversal. No cursor remains open across a yield, including early exits.
        Internal callers may supply a validated exclusive address and total limit.

        Yields:
            At most batch_size rows from one selected computation at a time.

        Raises:
            ValueError: If the batch size is not a positive integer.
        """
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            msg = "Batch size must be a positive integer"
            raise ValueError(msg)
        remaining = limit
        for index, computation in enumerate(self.computations):
            if after is not None and index < after.computation:
                continue
            key = (
                after.key if after is not None and index == after.computation else None
            )
            while remaining is None or remaining > 0:
                size = batch_size if remaining is None else min(batch_size, remaining)
                rows = _read(self.connection, computation.statement(size, key))
                if not rows:
                    break
                addresses = tuple(
                    RowAddress(
                        index, tuple(row[column.name] for column in computation.keys)
                    )
                    for row in rows
                )
                yield RowBatch(
                    tuple(
                        tuple(row[column.name] for column in self.registration.columns)
                        for row in rows
                    ),
                    addresses,
                )
                key = addresses[-1].key
                if remaining is not None:
                    remaining -= len(rows)
                if len(rows) < size:
                    break

    def frame(self) -> pd.DataFrame:
        """Return a detached native frame using retained logical types and CRS."""
        return frame_from_rows(
            (row for batch in self.batches() for row in batch.rows),
            self.registration.storage_description(),
        )
