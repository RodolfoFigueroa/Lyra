"""Atomic metric values in the shared relational store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.data.relational import Cell, rows_from_validated
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.output_selection import ComputationReader, OutputSelection
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
        """Publish one invocation atomically, keeping independently reusable keys."""
        if not selections:
            return
        first = selections[0][0]
        registrations = tuple(
            Registration.from_description(
                first.node_id, name, description, output_scope=first.output_scope
            )
            for name, description in descriptions.items()
        )
        publications = _prepare_publications(
            selections,
            outputs,
            descriptions,
            location_inputs,
            {
                registration.output_name: registration.key
                for registration in registrations
            },
        )
        with self.store.schema_transaction() as connection:
            pending = self._pending(connection, publications, registrations)
            if pending:
                self.store.register(connection, registrations)
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

    def _pending(
        self,
        connection: sa.Connection,
        publications: list[_MetricPublication],
        registrations: tuple[Registration, ...],
    ) -> list[_MetricPublication]:
        names = {registration.output_name for registration in registrations}
        pending = []
        for publication in publications:
            header = publication.header
            try:
                saved = self.store.header(connection, header.value_key)
            except MissingValueError:
                pending.append(publication)
                continue
            if (
                saved.computation_identity() != header.computation_identity()
                or saved.output_rows is None
                or saved.output_rows.keys() != names
            ):
                msg = "Retained metric header or output registrations differ"
                raise DamagedDataError(msg)
            if (
                LocationInputs(self.store).bindings(connection, header.value_key)
                != publication.bindings
            ):
                msg = "Retained metric input bindings differ"
                raise DamagedDataError(msg)
            _check_partitions(saved, publication.partitions)
            for registration in registrations:
                ComputationReader(self.store, connection, registration).prepare(
                    saved, None
                )
        return pending

    def lookup(
        self,
        header: Header,
        output_name: str | None = None,
        *,
        required: bool = False,
        partitions: tuple[str, ...] | None = None,
        expected_partitions: tuple[str, ...] | None = None,
    ) -> NativeOutputs | None:
        """Reconstruct detached native tables using only retained descriptions.

        Cache decisions supply the exact expected partition set, including empty
        owners. Retrieval may separately restrict a complete value to a subset.

        Returns:
            Named tables, or None for an optional absent computation.

        Raises:
            DamagedDataError: If the computation disagrees with its inventory.
        """
        with self.store.engine.connect() as connection:
            # sqlite3 does not begin a read transaction on the first SELECT.
            # Connection close releases the snapshot before computation or publication.
            connection.exec_driver_sql("BEGIN")
            try:
                # Probe schema existence without loading unrelated metric revisions.
                self.store.load_registrations(
                    (), allow_uninitialized=not required, connection=connection
                )
                saved = self.store.header(
                    connection, header.value_key, required=required
                )
            except MissingValueError:
                return None
            if saved.computation_identity() != header.computation_identity():
                msg = "Metric header differs from required computation"
                raise DamagedDataError(msg)
            if expected_partitions is not None:
                _check_partitions(saved, expected_partitions)
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
            DamagedDataError: If required descriptions or partitions are incomplete.
            MissingValueError: If the named output is undeclared.
        """
        inventory = header.output_registrations
        if inventory is None:
            msg = "Incomplete metric output descriptions"
            raise DamagedDataError(msg)
        if output_name is not None and output_name not in inventory:
            raise MissingValueError(output_name)
        selected = {
            name: key
            for name, key in inventory.items()
            if output_name is None or name == output_name
        }
        self.store.load_registrations(tuple(selected.values()), connection=connection)
        registrations = {
            name: self.store.layout.descriptions[key] for name, key in selected.items()
        }
        if any(
            name != registration.output_name
            for name, registration in registrations.items()
        ):
            msg = "Retained metric output registration has the wrong name"
            raise DamagedDataError(msg)
        return {
            name: OutputSelection(
                connection,
                registration,
                header.owner_scope,
                (
                    ComputationReader(self.store, connection, registration).prepare(
                        header, partitions
                    ),
                ),
            ).frame()
            for name, registration in registrations.items()
        }


def _check_partitions(header: Header, partitions: tuple[str, ...]) -> None:
    """Match every location output's completion, including zero-row owners.

    Raises:
        DamagedDataError: If the stored partition set differs from the demand.
    """
    if header.output_scope == "location":
        expected = set(partitions)
        if header.output_rows is None or any(
            not isinstance(counts, dict) or counts.keys() != expected
            for counts in header.output_rows.values()
        ):
            msg = "Retained metric partitions differ from the requested owners"
            raise DamagedDataError(msg)


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
    inventory: dict[str, str],
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
            update={"output_rows": counts, "output_registrations": inventory}
        )
    return publications
