"""Atomic metric values in the shared relational store."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import sqlalchemy as sa
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.data.relational import rows_from_validated
from lyra.runtime.output_selection import OutputSelection, prepare_computation
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import DamagedDataError, Header, MissingValueError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.data.relational import StorageDescription
    from lyra.data.results import NativeOutputs
    from lyra.runtime.sqlite_store import SQLiteStore


class MetricTables:
    """Borrow a store for owned, producer-validated outputs."""

    def __init__(self, store: SQLiteStore) -> None:
        """Bind storage without acquiring a connection."""
        self.store = store

    def publish(
        self,
        header: Header,
        outputs: NativeOutputs,
        descriptions: Mapping[str, StorageDescription],
        partitions: tuple[str, ...] = (),
    ) -> None:
        """Retain every declared table together, preserving duplicate rows.

        Raises:
            ValueError: If names or scope do not match the declared contract.
        """
        if not descriptions or outputs.keys() != descriptions.keys():
            msg = "Expected exactly the declared outputs"
            raise ValueError(msg)
        registrations = tuple(
            Registration.from_description(
                header.node_id, name, description, output_scope=header.output_scope
            )
            for name, description in descriptions.items()
        )
        self.store.initialize(registrations)
        batches = {
            name: rows_from_validated(outputs[name], description)
            for name, description in descriptions.items()
        }
        partitions = tuple(sorted(set(partitions)))
        counts: dict[str, int | dict[str, int]] = {}
        for name, rows in batches.items():
            if header.output_scope == "unpartitioned":
                counts[name] = len(rows)
                continue
            owner = descriptions[name].columns.index("location_id")
            frequencies = Counter(row[owner] for row in rows)
            if frequencies.keys() - set(partitions):
                msg = "Metric owner was not supplied through the primary input"
                raise ValueError(msg)
            counts[name] = {group: frequencies[group] for group in partitions}
        publication = header.model_copy(update={"output_rows": counts})
        with self.store.publication(publication, partitions) as connection:
            if connection is None:
                self.lookup(header, required=True)
                return
            for registration in registrations:
                name = registration.output_name
                data = self.store.layout.tables[registration.key]
                rows = batches[name]
                if rows:
                    connection.execute(
                        data.table.insert(),
                        [
                            {
                                data.value_column: header.value_key,
                                **(
                                    {data.row_column: index}
                                    if header.output_scope == "unpartitioned"
                                    else {}
                                ),
                                **dict(
                                    zip(descriptions[name].columns, row, strict=True)
                                ),
                            }
                            for index, row in enumerate(rows)
                        ],
                    )

    def lookup(
        self,
        header: Header,
        output_name: str | None = None,
        *,
        required: bool = False,
        partitions: tuple[str, ...] | None = None,
    ) -> NativeOutputs | None:
        """Reconstruct detached native tables using only retained descriptions.

        Returns:
            Named tables, or None for an optional absent computation.

        Raises:
            DamagedDataError: If required metadata or rows are damaged.
        """
        self.store.load_registrations()
        with self.store.engine.connect() as connection:
            try:
                saved = self.store.header(
                    connection, header.value_key, required=required
                )
            except MissingValueError:
                return None
            if saved.model_copy(update={"output_rows": None}) != header:
                msg = "Metric header differs from required computation"
                raise DamagedDataError(msg)
            try:
                return self.read_connection(connection, saved, output_name, partitions)
            except (
                exc.DatabaseError,
                ValueError,
                KeyError,
                TypeError,
                GEOSException,
            ) as error:
                msg = "Damaged metric output data"
                raise DamagedDataError(msg) from error

    def read_connection(
        self,
        connection: sa.Connection,
        header: Header,
        output_name: str | None,
        partitions: tuple[str, ...] | None,
    ) -> NativeOutputs:
        """Read selected tables within a caller-owned transaction.

        Returns:
            Detached named native frames.

        Raises:
            ValueError: If required descriptions or partitions are incomplete.
            MissingValueError: If the named output is undeclared.
        """
        layout = self.store.layout
        registrations = {
            r.output_name: r
            for r in layout.descriptions.values()
            if r.node_id == header.node_id
            and r.kind == "metric"
            and r.output_scope == header.output_scope
        }
        if output_name is not None and output_name not in registrations:
            raise MissingValueError(output_name)
        counts = header.output_rows
        if counts is None or (
            counts.keys() != registrations.keys()
            if output_name is None
            else output_name not in counts
        ):
            msg = "Incomplete metric output descriptions"
            raise ValueError(msg)
        return {
            name: OutputSelection(
                connection,
                registration,
                header.owner_scope,
                (
                    prepare_computation(
                        self.store, connection, registration, header, partitions
                    ),
                ),
            ).frame()
            for name, registration in registrations.items()
            if output_name is None or name == output_name
        }
