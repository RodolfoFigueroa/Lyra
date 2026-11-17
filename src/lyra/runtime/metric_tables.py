"""Atomic metric values in the shared relational store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.data.relational import Cell, rows_from_validated
from lyra.runtime.location_inputs import InputBindings, LocationInputs
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
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
        outputs: NativeOutputs,
        descriptions: Mapping[str, StorageDescription],
        *,
        location_inputs: Mapping[str, InputBindings],
    ) -> None:
        """Publish one invocation atomically, keeping independently reusable keys.

        Raises:
            DamagedDataError: If retained bindings differ from the invocation.
        """
        if not selections:
            return
        publications = _prepare_publications(
            selections, outputs, descriptions, location_inputs
        )
        first = selections[0][0]
        registrations = tuple(
            Registration.from_description(
                first.node_id, name, description, output_scope=first.output_scope
            )
            for name, description in descriptions.items()
        )
        self.store.initialize(registrations)
        with self.store.transaction() as connection:
            pending = []
            for publication in publications:
                header = publication.header.model_copy(update={"output_rows": None})
                if self._lookup_connection(connection, header) is None:
                    pending.append(publication)
                elif (
                    LocationInputs(self.store).bindings(connection, header.value_key)
                    != publication.bindings
                ):
                    msg = "Retained metric input bindings differ"
                    raise DamagedDataError(msg)
            LocationInputs(self.store).validate_bindings(
                connection, tuple(p.bindings for p in pending)
            )
            for publication in pending:
                header = publication.header
                connection.execute(self.store.layout.results.insert(), header.record())
                if publication.partitions:
                    connection.execute(
                        self.store.layout.partitions.insert(),
                        [
                            {"value_key": header.value_key, "location_id": group}
                            for group in publication.partitions
                        ],
                    )
                connection.execute(
                    self.store.layout.metric_inputs.insert(),
                    {
                        "value_key": header.value_key,
                        "bindings": publication.bindings.model_dump_json(),
                    },
                )
                for registration in registrations:
                    name = registration.output_name
                    data = self.store.layout.tables[registration.key]
                    rows = publication.rows[name]
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
                                        zip(
                                            descriptions[name].columns, row, strict=True
                                        )
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
        """
        self.store.load_registrations()
        with self.store.engine.connect() as connection:
            return self._lookup_connection(
                connection,
                header,
                output_name,
                required=required,
                partitions=partitions,
            )

    def _lookup_connection(
        self,
        connection: sa.Connection,
        header: Header,
        output_name: str | None = None,
        *,
        required: bool = False,
        partitions: tuple[str, ...] | None = None,
    ) -> NativeOutputs | None:
        try:
            saved = self.store.header(connection, header.value_key, required=required)
        except MissingValueError:
            return None
        if saved.model_copy(update={"output_rows": None}) != header:
            msg = "Metric header differs from required computation"
            raise DamagedDataError(msg)
        LocationInputs(self.store).bindings(connection, header.value_key)
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


@dataclass
class _MetricPublication:
    header: Header
    partitions: tuple[str, ...]
    rows: dict[str, list[tuple[Cell, ...]]]
    bindings: InputBindings


def _publication_inventory(
    selections: tuple[tuple[Header, tuple[str, ...]], ...],
    outputs: NativeOutputs,
    descriptions: Mapping[str, StorageDescription],
    location_inputs: Mapping[str, InputBindings],
) -> tuple[list[_MetricPublication], dict[str, _MetricPublication]]:
    first = selections[0][0]
    keys = [header.value_key for header, _ in selections]
    if not descriptions or outputs.keys() != descriptions.keys():
        msg = "Expected exactly the declared outputs"
        raise ValueError(msg)
    if len(set(keys)) != len(keys) or set(keys) != set(location_inputs):
        msg = "Incomplete or duplicate metric publication inventory"
        raise ValueError(msg)
    publications = []
    owners = {}
    for header, groups in selections:
        partitions = tuple(sorted(set(groups)))
        if (header.node_id, header.output_scope, header.owner_scope) != (
            first.node_id,
            first.output_scope,
            first.owner_scope,
        ) or header.target_scope is not None:
            msg = "Metric publication scopes differ"
            raise ValueError(msg)
        if header.output_scope == "unpartitioned" and (
            partitions or len(selections) != 1
        ):
            msg = (
                "Unpartitioned publication requires one computation without partitions"
            )
            raise ValueError(msg)
        if set(partitions) & owners.keys():
            msg = "Metric publication partitions overlap"
            raise ValueError(msg)
        bindings = location_inputs[header.value_key]
        if set(bindings.groups) - set(partitions):
            msg = "Input bindings reference undeclared metric partitions"
            raise ValueError(msg)
        publication = _MetricPublication(
            header, partitions, {name: [] for name in outputs}, bindings
        )
        publications.append(publication)
        owners.update(dict.fromkeys(partitions, publication))
    return publications, owners


def _prepare_publications(
    selections: tuple[tuple[Header, tuple[str, ...]], ...],
    outputs: NativeOutputs,
    descriptions: Mapping[str, StorageDescription],
    location_inputs: Mapping[str, InputBindings],
) -> list[_MetricPublication]:
    publications, owners = _publication_inventory(
        selections, outputs, descriptions, location_inputs
    )
    first = selections[0][0]
    for name, frame in outputs.items():
        rows = rows_from_validated(frame, descriptions[name])
        if first.output_scope == "unpartitioned":
            publications[0].rows[name] = rows
        else:
            owner = descriptions[name].columns.index("location_id")
            for row in rows:
                group = str(row[owner])
                if group not in owners:
                    msg = "Metric owner was not supplied through the primary input"
                    raise ValueError(msg)
                owners[group].rows[name].append(row)
    for publication in publications:
        counts = {}
        for name, rows in publication.rows.items():
            if first.output_scope == "unpartitioned":
                counts[name] = len(rows)
            else:
                frequencies = dict.fromkeys(publication.partitions, 0)
                owner = descriptions[name].columns.index("location_id")
                for row in rows:
                    frequencies[str(row[owner])] += 1
                counts[name] = frequencies
        publication.header = publication.header.model_copy(
            update={"output_rows": counts}
        )
    return publications
