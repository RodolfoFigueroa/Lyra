"""Immutable operation mappings with explicit completion and provider roles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import exc

from lyra.contracts.operations import OPERATION_MAPPING_ADAPTER
from lyra.runtime.output_selection import _read
from lyra.runtime.sqlite_schema import Registration, StoredColumn
from lyra.runtime.sqlite_store import DamagedDataError, Header, MissingValueError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.runtime.sqlite_schema import DataTable
    from lyra.runtime.sqlite_store import SQLiteStore


def operation_registration(node_id: str) -> Registration:
    """Return the shared strict pair layout for one operation."""
    return Registration(
        node_id=node_id,
        output_name="pairs",
        kind="operation",
        output_scope="location",
        columns=tuple(
            StoredColumn(name=name, dtype="string", nullable=False, unit=None)
            for name in ("source_location_id", "location_id")
        ),
    )


def pair_completion(
    store: SQLiteStore,
    connection: sa.Connection,
    header: Header,
    saved: Header,
) -> tuple[DataTable, dict[str, int]]:
    """Check operation roles and the partition inventory against stored completion.

    Returns:
        The registered operation table and its committed counts.

    Raises:
        DamagedDataError: If retained completion disagrees with the selection.
    """
    counts = (saved.output_rows or {}).get("pairs")
    if (
        saved.model_copy(update={"output_rows": None})
        != header.model_copy(update={"output_rows": None})
        or saved.target_scope is None
        or not isinstance(counts, dict)
        or set(saved.output_rows or {}) != {"pairs"}
    ):
        msg = "Incomplete retained operation selection"
        raise DamagedDataError(msg)
    registration = operation_registration(saved.node_id)
    if store.read_registration(connection, registration.key) != registration:
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
    return store.layout.tables[registration.key], counts


def validate_pair_counts(
    connection: sa.Connection,
    data: DataTable,
    key: str,
    counts: dict[str, int],
    sources: tuple[str, ...] | None = None,
) -> None:
    """Verify selected pair counts before applying target restrictions.

    Raises:
        DamagedDataError: If a source or one of its pairs is missing.
    """
    if sources is not None and not set(sources) <= counts.keys():
        msg = "Missing retained operation sources"
        raise DamagedDataError(msg)
    table = data.table.c
    # Count before applying target restrictions: those restrict inspection, not
    # the operation's durable completion contract.
    query = (
        sa.select(
            table.source_location_id,
            sa.func.count().label("rows"),
            sa.func.count(table.location_id.distinct()).label("identities"),
        )
        .where(table[data.value_column] == key)
        .group_by(table.source_location_id)
    )
    if sources is not None:
        query = query.where(table.source_location_id.in_(sources))
    observed = _read(connection, query)
    actual = {row["source_location_id"]: row["rows"] for row in observed}
    expected = counts if sources is None else {s: counts[s] for s in sources}
    if (
        actual.keys() - expected.keys()
        or any(row["rows"] != row["identities"] for row in observed)
        or any(actual.get(source, 0) != count for source, count in expected.items())
    ):
        msg = "Incomplete or duplicate retained operation pairs"
        raise DamagedDataError(msg)


class OperationPairs:
    """Borrow a store, retaining mappings independently of provider records."""

    def __init__(self, store: SQLiteStore) -> None:
        """Bind an explicitly owned relational store."""
        self.store = store

    def lookup(
        self, header: Header, sources: tuple[str, ...], *, required: bool = False
    ) -> dict[str, tuple[str, ...]] | None:
        """Read a complete mapping, distinguishing absence from damaged completion.

        Returns:
            Detached completed groups, or None for an optional absent computation.
        """
        self.store.load_registrations()
        with self.store.engine.connect() as connection:
            return self.read_connection(connection, header, sources, required=required)

    def read_connection(
        self,
        connection: sa.Connection,
        header: Header,
        sources: tuple[str, ...],
        *,
        required: bool = False,
    ) -> dict[str, tuple[str, ...]] | None:
        """Read completed groups within a caller-owned connection.

        Returns:
            Completed mappings, or None for an optional absent computation.

        Raises:
            DamagedDataError: If required completion or pair rows are damaged.
        """
        try:
            saved = self.store.header(connection, header.value_key, required=required)
        except MissingValueError:
            return None
        try:
            return self._read_groups(connection, header, saved, sources)
        except (
            DamagedDataError,
            exc.DatabaseError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            msg = "Damaged operation mapping"
            raise DamagedDataError(msg) from error

    def _read_groups(
        self,
        connection: sa.Connection,
        header: Header,
        saved: Header,
        sources: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        data, counts = self._completion(connection, header, saved, sources)
        groups: dict[str, list[str]] = {source: [] for source in sources}
        for row in connection.execute(
            sa.select(data.table).where(
                data.table.c[data.value_column] == header.value_key
            )
        ).mappings():
            groups[row["source_location_id"]].append(row["location_id"])
        if any(len(groups[source]) != count for source, count in counts.items()):
            msg = "Incomplete operation pair rows"
            raise ValueError(msg)
        return self._normalize({k: tuple(v) for k, v in groups.items()}, sources)

    def _completion(
        self,
        connection: sa.Connection,
        header: Header,
        saved: Header,
        sources: tuple[str, ...],
    ) -> tuple[DataTable, dict[str, int]]:
        data, counts = pair_completion(self.store, connection, header, saved)
        if set(sources) != counts.keys():
            msg = "Operation completion partitions differ"
            raise DamagedDataError(msg)
        return data, counts

    def _already_published(
        self, connection: sa.Connection, header: Header, sources: tuple[str, ...]
    ) -> bool:
        try:
            saved = self.store.header(connection, header.value_key)
        except MissingValueError:
            return False
        data, counts = self._completion(connection, header, saved, sources)
        validate_pair_counts(connection, data, saved.value_key, counts)
        return True

    @staticmethod
    def _normalize(
        groups: Mapping[str, tuple[str, ...]], sources: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        checked = OPERATION_MAPPING_ADAPTER.validate_python(dict(groups))
        if (
            set(checked) - set(sources)
            or any(len(targets) != len(set(targets)) for targets in checked.values())
            or any(not isinstance(source, str) for source in sources)
        ):
            msg = "Invalid operation sources or duplicate targets"
            raise ValueError(msg)
        return {source: checked.get(source, ()) for source in sources}

    def publish(
        self,
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
        groups: Mapping[str, tuple[str, ...]],
    ) -> None:
        """Atomically publish successful groups across complete or partition keys.

        Raises:
            ValueError: If mappings or header roles are invalid.
        """
        sources = tuple(source for _, ids in selections for source in ids)
        normalized = self._normalize(groups, sources)
        if not selections:
            return
        registrations = tuple(
            {operation_registration(h.node_id) for h, _ in selections}
        )
        if any(h.target_scope is None or h.owner_scope is None for h, _ in selections):
            msg = "Operations require source and target scopes"
            raise ValueError(msg)
        if self.store.path.exists():
            self.store.load_registrations()
        if any(r.key not in self.store.layout.descriptions for r in registrations):
            self.store.initialize(registrations)
        else:
            for registration in registrations:
                self.store.layout.register(registration)
        with self.store.transaction() as connection:
            for header, ids in selections:
                if self._already_published(connection, header, ids):
                    continue
                publication = header.model_copy(
                    update={
                        "output_rows": {
                            "pairs": {source: len(normalized[source]) for source in ids}
                        }
                    }
                )
                connection.execute(
                    self.store.layout.results.insert(), publication.record()
                )
                if ids:
                    connection.execute(
                        self.store.layout.partitions.insert(),
                        [
                            {"value_key": header.value_key, "location_id": source}
                            for source in ids
                        ],
                    )
                data = self.store.layout.tables[
                    operation_registration(header.node_id).key
                ]
                rows = [
                    {
                        data.value_column: header.value_key,
                        "source_location_id": source,
                        "location_id": target,
                    }
                    for source in ids
                    for target in normalized[source]
                ]
                if rows:
                    connection.execute(data.table.insert(), rows)
