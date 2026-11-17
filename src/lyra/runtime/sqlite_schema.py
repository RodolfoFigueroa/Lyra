"""Offline relational definitions and retained logical schema registrations."""

from dataclasses import dataclass
from typing import Literal

import pyarrow as pa
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from lyra.contracts import outputs
from lyra.contracts.value_identity import value_key
from lyra.data.relational import StorageDescription


class StoredColumn(BaseModel):
    """Plugin-independent native column description."""

    model_config = ConfigDict(frozen=True)
    name: str
    dtype: Literal["string", "Int64", "Float64", "boolean", "geometry"]
    nullable: bool
    description: str | None = None
    unit: str | None = None


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
        return value_key([self.kind, self.node_id, self.output_name])

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

    def storage_description(self) -> StorageDescription:
        """Return a native reconstruction layout without loading producer code."""
        types = {
            "string": (pa.string(), "TEXT"),
            "Int64": (pa.int64(), "INTEGER"),
            "Float64": (pa.float64(), "REAL"),
            "boolean": (pa.bool_(), "INTEGER"),
            "geometry": (pa.binary(), "BLOB"),
        }
        return StorageDescription(
            metadata=self.table_schema,
            properties=pa.schema(
                [
                    pa.field(c.name, types[c.dtype][0], nullable=c.nullable)
                    for c in self.columns
                    if c.dtype != "geometry"
                ]
            ),
            columns=tuple(c.name for c in self.columns),
            pandas_dtypes=tuple(c.dtype for c in self.columns),
            sqlite_types=tuple(types[c.dtype][1] for c in self.columns),
            nullable=tuple(c.nullable for c in self.columns),
            crs=self.crs,
            geometry=self.geometry,
            descriptions=tuple(c.description for c in self.columns),
            units=tuple(c.unit for c in self.columns),
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
            sa.Column("result_ref", sa.Text, primary_key=True),
            sa.Column("execution_id", sa.Text, nullable=False, unique=True),
            sa.Column("report", sa.Text, nullable=False),
            sa.Column("description", sa.Text, nullable=False),
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
        names = [column.name for column in registration.columns]
        structural = registration.structural_columns()
        value_column = "value_key"
        while value_column in names:
            value_column = "_" + value_column
        row_column = "row_index"
        while row_column in {*names, value_column}:
            row_column = "_" + row_column
        types = {
            "string": sa.Text,
            "Int64": sa.Integer,
            "Float64": sa.REAL,
            "boolean": sa.Boolean,
            "geometry": sa.LargeBinary,
        }
        columns = [sa.Column(value_column, sa.Text, primary_key=True)]
        columns.extend(
            sa.Column(
                column.name,
                types[column.dtype],
                nullable=column.nullable,
                primary_key=column.name in structural,
            )
            for column in registration.columns
        )
        if registration.output_scope == "unpartitioned":
            columns.append(sa.Column(row_column, sa.Integer, primary_key=True))
        table = sa.Table("data_" + key, self.metadata, *columns)
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
                "groups_" + key,
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
        self.tables[key] = result
        self.descriptions[key] = registration
        return result
