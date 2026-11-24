"""Offline relational definitions and retained logical schema registrations."""

import sys
from dataclasses import dataclass
from functools import cached_property
from string import ascii_lowercase, ascii_uppercase
from typing import Literal

import pyarrow as pa
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from lyra.contracts import outputs
from lyra.contracts.units import ColumnUnit
from lyra.contracts.value_identity import value_key
from lyra.data.arrow_tables import base_schema
from lyra.data.relational import StorageDescription, logical_dtype
from lyra.runtime.planning.computation import executable_contract


class StoredColumn(BaseModel):
    """Plugin-independent native column description."""

    model_config = ConfigDict(frozen=True)
    name: str
    dtype: Literal["string", "Int64", "Float64", "boolean", "geometry"]
    nullable: bool
    description: str | None = None
    unit: ColumnUnit


class Registration(BaseModel):
    """One registered provider, operation pair table, or named metric output."""

    model_config = ConfigDict(frozen=True)
    node_id: str
    output_name: str
    kind: Literal["provider", "metric", "operation"]
    output_scope: Literal["location", "unpartitioned"]
    columns: tuple[StoredColumn, ...]
    table_schema: outputs.OutputMetadata | None = None
    crs: str | None = None
    geometry: str | None = None

    @property
    def key(self) -> str:
        """Stable registration address independent of value keys."""
        if self.kind == "metric":
            return value_key(self.model_dump(mode="json"))
        return value_key([self.kind, self.node_id, self.output_name])

    @property
    def table_key(self) -> str:
        """Address compatible metric rows independently of descriptive wording."""
        if self.kind != "metric":
            return self.key
        layout = self.model_dump(
            mode="json",
            exclude={"table_schema": True, "columns": {"__all__": {"description"}}},
        )
        if self.table_schema is not None:
            layout["table_schema"] = executable_contract(self.table_schema)
        return value_key(layout)

    @classmethod
    def from_description(
        cls,
        node_id: str,
        output_name: str,
        description: StorageDescription,
        *,
        kind: Literal["provider", "metric", "operation"] = "metric",
        output_scope: Literal["location", "unpartitioned"] = "location",
    ) -> "Registration":
        """Return retained logical metadata from a compiled producer layout."""
        return cls(
            table_schema=description.metadata,
            node_id=node_id,
            output_name=output_name,
            kind=kind,
            output_scope=output_scope,
            columns=tuple(
                StoredColumn.model_validate(
                    {
                        "name": name,
                        "dtype": dtype,
                        "nullable": nullable,
                        "description": text,
                        "unit": unit,
                    }
                )
                for name, dtype, nullable, text, unit in zip(
                    description.columns,
                    description.pandas_dtypes,
                    description.nullable,
                    description.descriptions,
                    description.units,
                    strict=True,
                )
            ),
            crs=description.crs,
            geometry=description.geometry,
        )

    @cached_property
    def arrow_schema(self) -> pa.Schema:
        """Scope-independent fields of this loaded registration."""
        return base_schema(self.storage_description())

    def storage_description(self) -> StorageDescription:
        """Return a native reconstruction layout without loading producer code."""
        types = {
            "string": "TEXT",
            "Int64": "INTEGER",
            "Float64": "REAL",
            "boolean": "INTEGER",
            "geometry": "BLOB",
        }
        return StorageDescription(
            metadata=self.table_schema,
            columns=tuple(c.name for c in self.columns),
            pandas_dtypes=tuple(c.dtype for c in self.columns),
            sqlite_types=tuple(types[c.dtype] for c in self.columns),
            nullable=tuple(c.nullable for c in self.columns),
            crs=self.crs,
            geometry=self.geometry,
            descriptions=tuple(c.description for c in self.columns),
            units=tuple(c.unit for c in self.columns),
        )

    @cached_property
    def forwarded(self) -> "Registration":
        """Logical layout of expanded target records.

        Raises:
            ValueError: If the provider is missing its public schema.
        """
        if self.table_schema is None:
            msg = "Missing provider table schema"
            raise ValueError(msg)
        forwarded_schema = outputs.forwarding_schema(self.table_schema)
        source_column = forwarded_schema.columns[0]
        source = StoredColumn.model_validate(
            source_column.model_dump() | {"dtype": logical_dtype(source_column.dtype)}
        )
        return Registration.model_validate(
            self.model_dump()
            | {
                "columns": (source, *self.columns),
                "table_schema": forwarded_schema,
            }
        )

    def structural_columns(self) -> list[str]:
        """Return structural keys after validating their declared types.

        Raises:
            ValueError: If required structural columns are absent or nullable.
        """
        names = [column.name for column in self.columns]
        structural = []
        if self.output_scope == "location":
            structural = ["location_id"]
            if self.kind == "metric":
                structural.append("row_key")
            elif self.kind == "operation":
                structural.append("source_location_id")
        if not names:
            msg = "A retained table requires at least one column"
            raise ValueError(msg)
        if (
            len(names) != len(set(names))
            or any(name not in names for name in structural)
            or any(
                column.nullable or column.dtype != "string"
                for column in self.columns
                if column.name in structural
            )
            or (self.kind != "metric" and self.output_scope != "location")
        ):
            msg = "Invalid structural storage columns"
            raise ValueError(msg)
        return structural


@dataclass(frozen=True)
class DataTable:
    """Physical table and private column names, separate from author columns."""

    table: sa.Table
    value_column: str
    row_column: str

    @property
    def logical_columns(self) -> tuple[sa.ColumnElement, ...]:
        """Project physical columns under their original case-sensitive names."""
        return tuple(c if c.name == c.key else c.label(c.key) for c in self.table.c)


def _column_names(names: list[str]) -> tuple[dict[str, str], str, str]:
    """Allocate unique SQLite identifiers while retaining logical column keys.

    Returns:
        Physical names and the private value and row identifiers.
    """
    fold = str.maketrans(ascii_uppercase, ascii_lowercase)
    reserved = {name.translate(fold) for name in names}

    def private_name(candidate: str) -> str:
        while candidate.translate(fold) in reserved:
            candidate = "_" + candidate
        reserved.add(candidate.translate(fold))
        return candidate

    value_column = private_name("value_key")
    row_column = private_name("row_index")
    physical = {}
    used = set()
    for name in names:
        folded = name.translate(fold)
        physical[name] = private_name(name) if folded in used else name
        used.add(folded)
    return physical, value_column, row_column


class Layout:
    """Construct shared and typed tables without opening a database."""

    def __init__(self) -> None:
        """Declare shared scoped relationships."""
        self.metadata = sa.MetaData()
        self.registrations = sa.Table(
            "storage_registrations",
            self.metadata,
            sa.Column("registration_key", sa.Text, primary_key=True),
            sa.Column("description", sa.Text, nullable=False),
        )
        self.final_results = sa.Table(
            "final_results",
            self.metadata,
            sa.Column("job_id", sa.Text, primary_key=True),
            sa.Column("report", sa.Text, nullable=False),
            sa.Column("description", sa.Text, nullable=False),
        )
        self.intervention_validations = sa.Table(
            "intervention_validations",
            self.metadata,
            sa.Column("validation_key", sa.Text, primary_key=True),
        )
        self.results = sa.Table(
            "stored_results",
            self.metadata,
            sa.Column("value_key", sa.Text, primary_key=True),
            sa.Column("node_id", sa.Text, nullable=False),
            sa.Column("output_scope", sa.Text, nullable=False),
            sa.Column("owner_scope", sa.Text),
            sa.Column("target_scope", sa.Text),
            sa.Column("output_rows", sa.Text),
            sa.Column("output_registrations", sa.Text),
            sa.CheckConstraint(
                "(output_scope = 'location' AND owner_scope IS NOT NULL) OR "
                "(output_scope = 'unpartitioned' AND owner_scope IS NULL "
                "AND target_scope IS NULL)"
            ),
            sa.UniqueConstraint("value_key", "output_scope"),
        )
        self.partitions = sa.Table(
            "stored_partitions",
            self.metadata,
            sa.Column("value_key", sa.Text, primary_key=True),
            sa.Column("location_id", sa.Text, primary_key=True),
            sa.Column(
                "output_scope", sa.Text, nullable=False, server_default="location"
            ),
            sa.CheckConstraint("output_scope = 'location'"),
            sa.ForeignKeyConstraint(
                ["value_key", "output_scope"],
                ["stored_results.value_key", "stored_results.output_scope"],
            ),
        )
        self.input_selections = sa.Table(
            "input_selections",
            self.metadata,
            sa.Column("selection_key", sa.Text, primary_key=True),
            sa.Column("description", sa.Text, nullable=False),
        )
        self.metric_inputs = sa.Table(
            "metric_inputs",
            self.metadata,
            sa.Column(
                "value_key",
                sa.Text,
                sa.ForeignKey("stored_results.value_key"),
                primary_key=True,
            ),
            sa.Column("bindings", sa.Text, nullable=False),
        )
        self._physical_tables: dict[str, DataTable] = {}
        self.tables: dict[str, DataTable] = {}
        self.descriptions: dict[str, Registration] = {}

    def register(self, registration: Registration) -> DataTable:
        """Return a reused typed table for an identical registration.

        Raises:
            ValueError: If a registration changes or structural columns are invalid.
        """
        key = registration.key
        if key in self.tables:
            if self.descriptions[key] != registration:
                msg = "Registration conflicts with retained schema"
                raise ValueError(msg)
            return self.tables[key]
        table_key = registration.table_key
        if table_key in self._physical_tables:
            data = self._physical_tables[table_key]
            self.tables[key] = data
            self.descriptions[key] = registration
            return data
        names = [column.name for column in registration.columns]
        structural = registration.structural_columns()
        physical, value_column, row_column = _column_names(names)
        types = {
            "string": sa.Text,
            "Int64": sa.Integer,
            "Float64": sa.REAL,
            "boolean": sa.Integer,
            "geometry": sa.LargeBinary,
        }
        columns = [sa.Column(value_column, sa.Text, primary_key=True)]
        columns.extend(
            sa.Column(
                physical[column.name],
                types[column.dtype],
                key=column.name,
                nullable=column.nullable,
                primary_key=column.name in structural,
            )
            for column in registration.columns
        )
        if registration.output_scope == "unpartitioned":
            columns.append(sa.Column(row_column, sa.Integer, primary_key=True))
        table = sa.Table(
            "data_" + table_key, self.metadata, *columns, sqlite_strict=True
        )
        for column in registration.columns:
            field = table.c[column.name]
            if column.dtype == "boolean":
                table.append_constraint(sa.CheckConstraint(field.in_((0, 1))))
            elif column.dtype == "Float64":
                table.append_constraint(
                    sa.CheckConstraint(
                        field.between(-sys.float_info.max, sys.float_info.max)
                    )
                )
        if registration.output_scope == "location":
            owner = (
                "source_location_id"
                if registration.kind == "operation"
                else "location_id"
            )
            table.append_constraint(
                sa.ForeignKeyConstraint(
                    [value_column, owner],
                    ["stored_partitions.value_key", "stored_partitions.location_id"],
                )
            )
            sa.Index(
                "groups_" + table_key,
                table.c[value_column],
                table.c[owner],
                *((table.c.location_id,) if registration.kind == "operation" else ()),
            )
        else:
            table.append_constraint(
                sa.ForeignKeyConstraint(
                    [value_column],
                    ["stored_results.value_key"],
                )
            )
        result = DataTable(table, value_column, row_column)
        self._physical_tables[table_key] = result
        self.tables[key] = result
        self.descriptions[key] = registration
        return result
