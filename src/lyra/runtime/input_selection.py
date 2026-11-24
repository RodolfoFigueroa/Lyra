"""Bounded retained input traversal using qualified records and immediate pairs."""

from __future__ import annotations

import heapq
from contextlib import ExitStack, closing
from dataclasses import dataclass
from itertools import groupby, islice
from typing import TYPE_CHECKING

import sqlalchemy as sa

from lyra.contracts.provider_scope import RecordReferences
from lyra.data.relational import frame_from_rows
from lyra.runtime.operation_pairs import pair_completion, validate_pair_counts
from lyra.runtime.output_selection import _read
from lyra.runtime.sqlite_store import DamagedDataError, Header

if TYPE_CHECKING:
    from collections.abc import Generator

    import pandas as pd

    from lyra.data.relational import Cell
    from lyra.runtime.location_inputs import InputSelection, OperationSelection
    from lyra.runtime.sqlite_schema import DataTable, Registration
    from lyra.runtime.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class PairRows:
    """One indexed operation selection, before joining qualified target values."""

    data: DataTable
    operation: OperationSelection

    def select(self, *columns: sa.ColumnElement) -> sa.Select:
        """Return SQL restricted to the retained immediate source and target groups."""
        table = self.data.table.c
        query = sa.select(*columns).where(
            table[self.data.value_column] == self.operation.header.value_key,
            table.source_location_id.in_(self.operation.sources),
        )
        if self.operation.targets is not None:
            query = query.where(table.location_id.in_(self.operation.targets))
        return query

    def pairs(self, connection: sa.Connection, size: int) -> Generator[tuple[str, str]]:
        """Yield ordered pairs from bounded cursor partitions."""
        source, target = (
            self.data.table.c.source_location_id,
            self.data.table.c.location_id,
        )
        query = self.select(source, target).order_by(source, target)
        with closing(connection.execute(query)) as cursor:
            for rows in cursor.mappings().partitions(size):
                for row in rows:
                    yield row["source_location_id"], row["location_id"]


def prepare_pairs(
    store: SQLiteStore, connection: sa.Connection, operation: OperationSelection
) -> PairRows:
    """Validate completion and selected pair counts without loading target values.

    Returns:
        An ordered reader for the selected pairs.
    """
    saved = store.header(connection, operation.header.value_key, required=True)
    data, counts = pair_completion(store, connection, operation.header, saved)
    validate_pair_counts(connection, data, saved.value_key, counts, operation.sources)
    return PairRows(data, operation)


@dataclass(frozen=True)
class InputRows:
    """One logical input union borrowing a connection for its entire traversal.

    Membership is retained metadata. Only bounded SQL batches of pairs and target
    values are materialized. Pair streams merge by immediate identity so overlap
    never duplicates a row, while targets under distinct sources remain separate.
    """

    store: SQLiteStore
    connection: sa.Connection
    selections: tuple[InputSelection, ...]
    records: tuple[str, ...]
    references: RecordReferences
    operations: tuple[PairRows, ...]

    @classmethod
    def prepare(
        cls,
        store: SQLiteStore,
        connection: sa.Connection,
        selections: tuple[InputSelection, ...],
    ) -> InputRows:
        """Return a batched row reader for selections with matching metadata."""
        first = selections[0]
        return cls(
            store,
            connection,
            selections,
            tuple(
                sorted(
                    {record for selection in selections for record in selection.records}
                )
            ),
            RecordReferences(
                first.description.provider_scope,
                frozenset(location for s in selections for location in s.added_ids),
            ),
            tuple(
                prepare_pairs(store, connection, operation)
                for selection in selections
                for operation in selection.operations
            ),
        )

    @property
    def registration(self) -> Registration:
        """Captured logical forwarding or provider schema."""
        return self.selections[0].registration

    def _members(self, size: int) -> Generator[tuple[str, ...]]:
        if self.selections[0].description.kind == "provider":
            for record in self.records:
                yield (record,)
            return
        with ExitStack() as stack:
            streams = [
                stack.enter_context(closing(operation.pairs(self.connection, size)))
                for operation in self.operations
            ]
            for pair, _ in groupby(heapq.merge(*streams)):
                yield pair

    def _records(
        self, members: list[tuple[str, ...]], *, identities_only: bool = False
    ) -> dict[str, sa.RowMapping]:
        references = self.references.by_key(member[-1] for member in members)
        expected = {key: ref.location_id for key, ref in references.items()}
        registration = self.registration
        retained = self.store.read_registration(self.connection, registration.key)
        columns = tuple(
            c for c in registration.columns if c.name != "source_location_id"
        )
        if (
            retained.columns != columns
            or retained.crs != registration.crs
            or retained.geometry != registration.geometry
        ):
            msg = "Retained provider schema differs"
            raise DamagedDataError(msg)
        headers = _read(
            self.connection,
            sa.select(self.store.layout.results)
            .where(self.store.layout.results.c.value_key.in_(expected))
            .limit(len(expected) + 1),
        )
        if len(headers) != len(expected) or any(
            dict(row)
            != Header.for_record(
                row["value_key"], references[row["value_key"]]
            ).record()
            for row in headers
        ):
            msg = "Missing or damaged retained provider scope"
            raise DamagedDataError(msg)
        partitions = self.store.layout.partitions
        owners = _read(
            self.connection,
            sa.select(partitions)
            .where(partitions.c.value_key.in_(expected))
            .limit(len(expected) + 1),
        )
        if (
            len(owners) != len(expected)
            or {row["value_key"]: row["location_id"] for row in owners} != expected
        ):
            msg = "Incomplete retained provider partitions"
            raise DamagedDataError(msg)
        data = self.store.layout.tables[registration.key]
        projection = (
            (data.table.c[data.value_column], data.table.c.location_id)
            if identities_only
            else data.logical_columns
        )
        rows = _read(
            self.connection,
            sa.select(*projection)
            .where(data.table.c[data.value_column].in_(expected))
            .limit(len(expected) + 1),
        )
        if (
            len(rows) != len(expected)
            or {row[data.value_column]: row["location_id"] for row in rows} != expected
        ):
            msg = "Incomplete retained provider records"
            raise DamagedDataError(msg)
        return {row["location_id"]: row for row in rows}

    def validate_records(self) -> None:
        """Verify each selected provider row using only its retained identity."""
        with closing(self._members(1024)) as members:
            while batch := list(islice(members, 1024)):
                self._records(batch, identities_only=True)

    def batches(
        self, batch_size: int = 1024
    ) -> Generator[tuple[tuple[Cell, ...], ...]]:
        """Yield bounded joined cells.

        Raises:
            ValueError: If the batch size is not a positive integer.
        """
        if type(batch_size) is not int or batch_size < 1:
            msg = "Batch size must be a positive integer"
            raise ValueError(msg)
        with closing(self._members(batch_size)) as members:
            while selected := list(islice(members, batch_size)):
                records = self._records(selected)
                yield tuple(
                    tuple(
                        member[0]
                        if column.name == "source_location_id"
                        else records[member[-1]][column.name]
                        for column in self.registration.columns
                    )
                    for member in selected
                )

    def frame(self) -> pd.DataFrame:
        """Return a detached native frame using the same cells as Arrow streams."""
        with closing(self.batches()) as batches:
            return frame_from_rows(
                (row for batch in batches for row in batch),
                self.registration.storage_description(),
            )
