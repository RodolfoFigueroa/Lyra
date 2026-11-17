"""Immutable operation mappings with explicit completion and provider roles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import exc

from lyra.contracts.operations import OPERATION_MAPPING_ADAPTER
from lyra.runtime.sqlite_schema import Registration, StoredColumn
from lyra.runtime.sqlite_store import DamagedDataError, Header, MissingValueError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.runtime.sqlite_store import SQLiteStore


def operation_registration(node_id: str) -> Registration:
    """Return the shared strict pair layout for one operation."""
    return Registration(
        node_id=node_id,
        output_name="pairs",
        kind="operation",
        output_scope="location",
        columns=tuple(
            StoredColumn(name=name, dtype="string", nullable=False)
            for name in ("source_location_id", "location_id")
        ),
    )


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
        except (exc.DatabaseError, ValueError, KeyError, TypeError) as error:
            msg = "Damaged operation mapping"
            raise DamagedDataError(msg) from error

    def _read_groups(
        self,
        connection: sa.Connection,
        header: Header,
        saved: Header,
        sources: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        if (
            saved.model_copy(update={"output_rows": None})
            != header.model_copy(update={"output_rows": None})
            or saved.target_scope is None
        ):
            msg = "Operation provider roles differ"
            raise ValueError(msg)
        layout = self.store.layout
        registration = operation_registration(header.node_id)
        if layout.descriptions[registration.key] != registration:
            msg = "Operation registration differs"
            raise ValueError(msg)
        data = layout.tables[registration.key]
        partitions = list(
            connection.execute(
                sa.select(layout.partitions).where(
                    layout.partitions.c.value_key == header.value_key
                )
            ).mappings()
        )
        owners = [row["location_id"] for row in partitions]
        if set(owners) != set(sources) or len(owners) != len(set(owners)):
            msg = "Operation completion partitions differ"
            raise ValueError(msg)
        groups: dict[str, list[str]] = {source: [] for source in owners}
        counts = saved.output_rows
        if (
            counts is None
            or set(counts) != {"pairs"}
            or not isinstance(counts["pairs"], dict)
            or set(counts["pairs"]) != set(owners)
        ):
            msg = "Missing operation pair completion counts"
            raise ValueError(msg)
        for row in connection.execute(
            sa.select(data.table).where(
                data.table.c[data.value_column] == header.value_key
            )
        ).mappings():
            groups[row["source_location_id"]].append(row["location_id"])
        if any(
            len(groups[source]) != count for source, count in counts["pairs"].items()
        ):
            msg = "Incomplete operation pair rows"
            raise ValueError(msg)
        return self._normalize({k: tuple(v) for k, v in groups.items()}, sources)

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
                if self.read_connection(connection, header, ids) is not None:
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
