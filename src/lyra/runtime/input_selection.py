"""Bounded retained input traversal using qualified records and immediate pairs."""

from __future__ import annotations

import heapq
from bisect import bisect_right
from contextlib import ExitStack, closing
from dataclasses import dataclass
from itertools import groupby, islice
from typing import TYPE_CHECKING

import sqlalchemy as sa

from lyra.contracts.provider_scope import RecordReferences
from lyra.data.relational import frame_from_rows
from lyra.runtime.operation_pairs import operation_registration
from lyra.runtime.output_selection import RowAddress, RowBatch, _read
from lyra.runtime.sqlite_store import DamagedDataError, Header

if TYPE_CHECKING:
    from collections.abc import Generator

    import pandas as pd

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

    def pairs(
        self, connection: sa.Connection, size: int, after: tuple[str | int, ...] | None
    ) -> Generator[tuple[str, str]]:
        """Yield bounded keyset batches; no SQL cursor survives a yield."""
        source, target = (
            self.data.table.c.source_location_id,
            self.data.table.c.location_id,
        )
        while True:
            query = self.select(source, target).order_by(source, target).limit(size)
            if after is not None:
                query = query.where(
                    sa.or_(
                        source > after[0],
                        sa.and_(source == after[0], target > after[1]),
                    )
                )
            rows = _read(connection, query)
            for row in rows:
                yield row["source_location_id"], row["location_id"]
            if len(rows) < size:
                return
            after = (rows[-1]["source_location_id"], rows[-1]["location_id"])


def prepare_pairs(
    store: SQLiteStore, connection: sa.Connection, operation: OperationSelection
) -> PairRows:
    """Validate completion and selected pair counts without loading target values.

    Returns:
        A restricted reader with an indexed private continuation.

    Raises:
        DamagedDataError: If required operation metadata or selected pairs differ.
    """
    saved = store.header(connection, operation.header.value_key, required=True)
    counts = (saved.output_rows or {}).get("pairs")
    if (
        saved.model_copy(update={"output_rows": None})
        != operation.header.model_copy(update={"output_rows": None})
        or saved.target_scope is None
        or not isinstance(counts, dict)
        or set(saved.output_rows or {}) != {"pairs"}
        or not set(operation.sources) <= counts.keys()
    ):
        msg = "Incomplete retained operation selection"
        raise DamagedDataError(msg)
    registration = operation_registration(saved.node_id)
    store.load_registrations((registration.key,))
    if store.layout.descriptions[registration.key] != registration:
        msg = "Retained operation schema differs"
        raise DamagedDataError(msg)
    partitions = store.layout.partitions
    owners = _read(
        connection,
        sa.select(partitions.c.location_id).where(
            partitions.c.value_key == saved.value_key
        ),
    )
    if (
        len(owners) != len(counts)
        or {row["location_id"] for row in owners} != counts.keys()
    ):
        msg = "Incomplete operation partitions"
        raise DamagedDataError(msg)
    data = store.layout.tables[registration.key]
    table = data.table.c
    # Count before applying target restrictions: those restrict inspection, not
    # the operation's durable completion contract.
    observed = _read(
        connection,
        sa.select(
            table.source_location_id,
            sa.func.count().label("rows"),
            sa.func.count(table.location_id.distinct()).label("identities"),
        )
        .where(
            table[data.value_column] == saved.value_key,
            table.source_location_id.in_(operation.sources),
        )
        .group_by(table.source_location_id),
    )
    actual = {row["source_location_id"]: row["rows"] for row in observed}
    if any(row["rows"] != row["identities"] for row in observed) or any(
        actual.get(source, 0) != counts[source] for source in operation.sources
    ):
        msg = "Incomplete or duplicate retained operation pairs"
        raise DamagedDataError(msg)
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

    def contains(self, address: RowAddress) -> bool:
        """Return whether a private cursor identifies a selected record or pair."""
        if address.computation != 0 or any(type(key) is not str for key in address.key):
            return False
        if self.selections[0].description.kind == "provider":
            return len(address.key) == 1 and address.key[0] in self.records
        if len(address.key) != 2:
            return False
        return any(
            _read(
                self.connection,
                operation.select(operation.data.table.c.location_id)
                .where(
                    operation.data.table.c.source_location_id == address.key[0],
                    operation.data.table.c.location_id == address.key[1],
                )
                .limit(1),
            )
            for operation in self.operations
        )

    def _members(
        self, size: int, after: RowAddress | None
    ) -> Generator[tuple[str, ...]]:
        if self.selections[0].description.kind == "provider":
            start = (
                0 if after is None else bisect_right(self.records, str(after.key[0]))
            )
            for index in range(start, len(self.records)):
                yield (self.records[index],)
            return
        with ExitStack() as stack:
            streams = [
                stack.enter_context(
                    closing(
                        operation.pairs(
                            self.connection, size, None if after is None else after.key
                        )
                    )
                )
                for operation in self.operations
            ]
            for pair, _ in groupby(heapq.merge(*streams)):
                yield pair

    def _records(self, members: list[tuple[str, ...]]) -> dict[str, sa.RowMapping]:
        references = self.references.by_key(member[-1] for member in members)
        expected = {key: ref.location_id for key, ref in references.items()}
        registration = self.registration
        self.store.load_registrations((registration.key,))
        retained = self.store.layout.descriptions[registration.key]
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
        rows = _read(
            self.connection,
            sa.select(*data.logical_columns)
            .where(data.table.c[data.value_column].in_(expected))
            .limit(len(expected) + 1),
        )
        if (
            len(rows) != len(expected)
            or {row[data.value_column]: row["location_id"] for row in rows} != expected
            or any(
                row[c.name] is None for row in rows for c in columns if not c.nullable
            )
        ):
            msg = "Incomplete retained provider records"
            raise DamagedDataError(msg)
        return {row["location_id"]: row for row in rows}

    def batches(
        self,
        batch_size: int = 1024,
        *,
        after: RowAddress | None = None,
        limit: int | None = None,
    ) -> Generator[RowBatch]:
        """Yield bounded joined cells, releasing readers on failure or early close.

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
        size = batch_size if limit is None else min(batch_size, limit)
        remaining = limit
        with closing(self._members(size, after)) as members:
            while remaining is None or remaining > 0:
                selected = list(
                    islice(members, size if remaining is None else min(size, remaining))
                )
                if not selected:
                    return
                records = self._records(selected)
                yield RowBatch(
                    tuple(
                        tuple(
                            member[0]
                            if column.name == "source_location_id"
                            else records[member[-1]][column.name]
                            for column in self.registration.columns
                        )
                        for member in selected
                    ),
                    tuple(RowAddress(0, member) for member in selected),
                )
                if remaining is not None:
                    remaining -= len(selected)

    def frame(self) -> pd.DataFrame:
        """Return a detached native frame using the same cells as Arrow streams."""
        with closing(self.batches()) as batches:
            return frame_from_rows(
                (row for batch in batches for row in batch.rows),
                self.registration.storage_description(),
            )
