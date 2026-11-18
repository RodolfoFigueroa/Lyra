"""Shared immutable selections of retained provider records and operation groups."""

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lyra.contracts.provider_scope import EffectiveScope
from lyra.contracts.references import RetrievalName
from lyra.contracts.results import LocationInputDescription
from lyra.contracts.value_identity import value_key
from lyra.data.relational import frame_from_rows
from lyra.runtime.input_selection import InputRows
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import DamagedDataError, Header

if TYPE_CHECKING:
    from collections.abc import Generator

    import geopandas as gpd

    from lyra.runtime.sqlite_store import SQLiteStore


class OperationSelection(BaseModel):
    """Selected immediate source groups and optional target restriction."""

    model_config = ConfigDict(frozen=True)
    header: Header
    sources: tuple[str, ...]
    targets: tuple[str, ...] | None = None


class InputSelection(BaseModel):
    """Captured logical schema and shared membership, without copied values."""

    model_config = ConfigDict(frozen=True)
    description: LocationInputDescription
    registration: Registration
    records: tuple[str, ...] = ()
    operations: tuple[OperationSelection, ...] = ()
    added_ids: tuple[str, ...] = Field(default=(), exclude_if=lambda ids: not ids)

    @model_validator(mode="after")
    def validate_selection(self) -> "InputSelection":
        """Validate provider roles and the captured reconstruction schema.

        Returns:
            The checked immutable selection.

        Raises:
            ValueError: If membership or schema conflicts with the description.
        """
        desc, registration = self.description, self.registration
        names = [c.name for c in registration.columns]
        schema_invalid = (
            registration.node_id != desc.provider_scope.provider_id
            or registration.kind != "provider"
            or registration.table_schema != desc.table_schema
            or registration.geometry is None
            or "row_key" in names
        )
        membership_invalid = (
            ("source_location_id" in names) != (desc.kind == "correspondence")
            or len(set(self.records)) != len(self.records)
            or (
                bool(self.added_ids)
                and not isinstance(desc.provider_scope, EffectiveScope)
            )
            or tuple(sorted(set(self.added_ids))) != self.added_ids
            or (
                desc.kind == "provider"
                and not set(self.added_ids).issubset(self.records)
            )
            or bool(self.operations if desc.kind == "provider" else self.records)
            or (
                desc.availability == "not_materialized"
                and bool(self.records or self.operations or self.added_ids)
            )
        )
        roles_invalid = any(
            op.header.owner_scope != desc.source_scope
            or op.header.target_scope != desc.provider_scope
            or len(set(op.sources)) != len(op.sources)
            for op in self.operations
        )
        if schema_invalid or membership_invalid or roles_invalid:
            msg = "Invalid retained input selection"
            raise ValueError(msg)
        registration.structural_columns()
        return self

    @property
    def key(self) -> str:
        """Internal content address shared across metric partitions."""
        return value_key(self.model_dump(mode="json"))


class InputBindings(BaseModel):
    """Explicit inventory and references retained atomically with a computation."""

    model_config = ConfigDict(frozen=True)
    names: tuple[RetrievalName, ...] = ()
    selections: dict[RetrievalName, tuple[str, ...]] = Field(default_factory=dict)

    groups: dict[str, dict[RetrievalName, tuple[str, ...]]] = Field(
        default_factory=dict
    )

    def selected(self, groups: tuple[str, ...] | None = None) -> "InputBindings":
        """Combine shared references with selected metric groups.

        Returns:
            Flat references without copying shared memberships.

        Raises:
            DamagedDataError: If a requested group has no retained bindings.
        """
        declared = set(self.names)
        if any(set(bindings) - declared for bindings in self.groups.values()):
            msg = "Undeclared grouped location input"
            raise DamagedDataError(msg)
        selected = tuple(self.groups) if groups is None else groups
        if self.groups and not set(selected) <= self.groups.keys():
            msg = "Missing metric input groups"
            raise DamagedDataError(msg)
        references = {name: list(keys) for name, keys in self.selections.items()}
        for group in selected:
            for name, keys in self.groups.get(group, {}).items():
                references.setdefault(name, []).extend(keys)
        result = InputBindings(
            names=self.names,
            selections={
                name: tuple(dict.fromkeys(keys)) for name, keys in references.items()
            },
        )
        result.validate_inventory()
        return result

    def validate_inventory(self) -> None:
        """Require complete named references, including an explicit empty inventory.

        Raises:
            DamagedDataError: If declared inputs are missing or duplicated.
        """
        if (
            len(set(self.names)) != len(self.names)
            or set(self.names) != set(self.selections)
            or any(not refs for refs in self.selections.values())
        ):
            msg = "Incomplete retained input inventory"
            raise DamagedDataError(msg)


class LocationInputs:
    """Read and retain selections without current provider registrations."""

    def __init__(self, store: "SQLiteStore") -> None:
        """Borrow the relational store."""
        self.store = store

    def publish(self, selection: InputSelection) -> str:
        """Validate required values and retain one deduplicated selection.

        Returns:
            The private reconstruction reference.
        """
        selection = InputSelection.model_validate_json(selection.model_dump_json())
        if (
            selection.description.kind == "provider"
            and selection.registration.key not in self.store.layout.descriptions
        ):
            self.store.initialize((selection.registration,))

        with self.store.transaction() as connection:
            frame_from_rows(
                self.rows(connection, selection),
                selection.registration.storage_description(),
            )
            table = self.store.layout.input_selections
            if (
                connection.execute(
                    sa.select(table).where(table.c.selection_key == selection.key)
                )
                .mappings()
                .one_or_none()
                is None
            ):
                connection.execute(
                    table.insert(),
                    {
                        "selection_key": selection.key,
                        "description": json.dumps(
                            {
                                **selection.model_dump(
                                    mode="json",
                                    exclude={
                                        "registration": True,
                                        "description": {"table_schema"},
                                    },
                                ),
                                "registration_key": selection.registration.key,
                            }
                        ),
                    },
                )
        return selection.key

    def load(self, connection: sa.Connection, key: str) -> "InputSelection":
        """Load captured metadata and membership without reading geometries.

        Returns:
            A detached selection.
        """
        return self.borrow_selection(connection, key).model_copy(deep=True)

    def borrow_selection(self, connection: sa.Connection, key: str) -> InputSelection:
        """Decode owned metadata for internal traversal.

        Returns:
            A checked selection.

        Raises:
            DamagedDataError: If the address or reference is damaged.
        """
        table = self.store.layout.input_selections
        row = (
            connection.execute(sa.select(table).where(table.c.selection_key == key))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DamagedDataError(key)
        payload = json.loads(row["description"])
        if not isinstance(payload, dict) or not isinstance(
            payload.get("description"), dict
        ):
            msg = "Invalid retained input selection"
            raise DamagedDataError(msg)
        registration_key = payload.pop("registration_key")
        registration = self._registration(
            connection,
            registration_key,
            derived=payload["description"]["kind"] == "correspondence",
        )
        payload["description"]["table_schema"] = registration.table_schema
        selection = InputSelection.model_validate(
            {**payload, "registration": registration}
        )
        if selection.key != key:
            msg = "Retained input selection address differs"
            raise DamagedDataError(msg)
        return selection

    def _registration(
        self, connection: sa.Connection, key: str, *, derived: bool
    ) -> Registration:
        base = self.store.read_registration(connection, key)
        return base.forwarded() if derived else base

    def descriptions(
        self, connection: sa.Connection, bindings: InputBindings
    ) -> dict[str, LocationInputDescription]:
        """Project complete independent descriptions without loading value rows.

        Returns:
            Public named input descriptions.

        Raises:
            DamagedDataError: If combined selections disagree.
        """
        bindings = bindings.selected() if bindings.groups else bindings
        bindings.validate_inventory()
        result = {}
        for name, keys in bindings.selections.items():
            descriptions = [
                self.borrow_selection(connection, key).description for key in keys
            ]
            if any(desc != descriptions[0] for desc in descriptions):
                msg = "Combined input descriptions differ"
                raise DamagedDataError(msg)
            result[name] = descriptions[0].model_copy(deep=True)
        return result

    def validate_bindings(
        self, connection: sa.Connection, bindings: tuple[InputBindings, ...]
    ) -> None:
        """Validate distinct retained selections before writing metric values."""
        with _input_traversal(self.store, connection) as inputs:
            references: dict[str, None] = {}
            for inventory in bindings:
                inputs.descriptions(connection, inventory)
                for keys in inventory.selected().selections.values():
                    references.update(dict.fromkeys(keys))
            for key in references:
                selection = inputs.borrow_selection(connection, key)
                frame_from_rows(
                    self.rows(connection, selection),
                    selection.registration.storage_description(),
                )

    def bindings(self, connection: sa.Connection, key: str) -> InputBindings:
        """Read a computation's complete named bindings independently of reports.

        Returns:
            Validated references for reuse and selected result assembly.
        """
        with _input_traversal(self.store, connection) as inputs:
            return inputs.borrow_bindings(connection, key).model_copy(deep=True)

    def borrow_bindings(self, connection: sa.Connection, key: str) -> InputBindings:
        """Validate a complete computation inventory for internal assembly.

        Returns:
            Complete references.

        Raises:
            DamagedDataError: If completion or references are absent.
        """
        header = self.store.header(connection, key, required=True)
        if header.output_rows is None or header.target_scope is not None:
            msg = "Missing metric input completion"
            raise DamagedDataError(msg)
        table = self.store.layout.metric_inputs
        row = (
            connection.execute(sa.select(table).where(table.c.value_key == key))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DamagedDataError(key)
        bindings = InputBindings.model_validate_json(row["bindings"])
        self.descriptions(connection, bindings)
        for keys in bindings.selected().selections.values():
            for selection_key in keys:
                selection = self.borrow_selection(connection, selection_key)
                self._check_operations(connection, selection)
        return bindings

    def _check_operations(
        self, connection: sa.Connection, selection: InputSelection, /
    ) -> None:
        for operation in selection.operations:
            saved = self.store.header(
                connection, operation.header.value_key, required=True
            )
            counts = (saved.output_rows or {}).get("pairs")
            if (
                saved.model_copy(update={"output_rows": None})
                != operation.header.model_copy(update={"output_rows": None})
                or not isinstance(counts, dict)
                or not set(operation.sources) <= counts.keys()
            ):
                msg = "Damaged retained input operation relationship"
                raise DamagedDataError(msg)

    def rows(self, connection: sa.Connection, selection: InputSelection) -> list[tuple]:
        """Select qualified native cells, preserving immediate pairs and restrictions.

        Returns:
            Cells in the captured schema's order; no frame is required for selection.
        """
        selected = InputRows.prepare(self.store, connection, (selection,))
        return [row for batch in selected.batches() for row in batch.rows]

    def frame(
        self, connection: sa.Connection, keys: tuple[str, ...]
    ) -> "gpd.GeoDataFrame":
        """Reconstruct a detached native union using captured nullable types.

        Returns:
            A geographic input without metric row identity.
        """
        selections = tuple(self.load(connection, key) for key in dict.fromkeys(keys))
        return cast(
            "gpd.GeoDataFrame",
            InputRows.prepare(self.store, connection, selections).frame(),
        )


class _InputTraversal(LocationInputs):
    """Private borrowed snapshots for exactly one store and connection traversal.

    Callers end this scope before writes, commit, rollback, or connection release.
    Neither the store nor the connection retains this object.
    """

    def __init__(self, store: "SQLiteStore", connection: sa.Connection) -> None:
        super().__init__(store)
        self._connection: sa.Connection | None = connection
        self._registrations: dict[tuple[str, bool], Registration] = {}
        self._selections: dict[str, InputSelection] = {}
        self._complete_bindings: dict[str, InputBindings] = {}

    def clear(self) -> None:
        self._registrations.clear()
        self._selections.clear()
        self._complete_bindings.clear()
        self._connection = None

    def _check_connection(self, connection: sa.Connection) -> None:
        if connection is not self._connection:
            msg = (
                "Retained metadata traversal is closed or belongs to another connection"
            )
            raise RuntimeError(msg)

    def _registration(
        self, connection: sa.Connection, key: str, *, derived: bool
    ) -> Registration:
        address = (key, derived)
        if address not in self._registrations:
            self._registrations[address] = super()._registration(
                connection, key, derived=derived
            )
        return self._registrations[address]

    def borrow_selection(self, connection: sa.Connection, key: str) -> InputSelection:
        self._check_connection(connection)
        if key not in self._selections:
            selection = super().borrow_selection(connection, key)
            super()._check_operations(connection, selection)
            self._selections[key] = selection
        return self._selections[key]

    def _check_operations(
        self, connection: sa.Connection, _selection: InputSelection, /
    ) -> None:
        # Selection loading checks relationships before admitting a snapshot.
        self._check_connection(connection)

    def borrow_bindings(self, connection: sa.Connection, key: str) -> InputBindings:
        self._check_connection(connection)
        if key not in self._complete_bindings:
            bindings = super().borrow_bindings(connection, key)
            self._complete_bindings[key] = bindings
        return self._complete_bindings[key]


@contextmanager
def _input_traversal(
    store: "SQLiteStore", connection: sa.Connection
) -> "Generator[_InputTraversal]":
    """Own metadata until the surrounding connection scope ends.

    Yields:
        Private borrowed snapshots, cleared before commit or connection release.
    """
    inputs = _InputTraversal(store, connection)
    try:
        yield inputs
    finally:
        inputs.clear()
