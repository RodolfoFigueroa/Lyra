"""Generated descriptions of executable output schemas."""

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject
from lyra.contracts.units import ColumnUnit


class CheckMetadata(StrictBaseModel):
    """Descriptive check information, never executable validation."""

    name: str
    description: str | None = None
    kind: Literal["builtin", "python"]
    parameters: JsonObject = Field(default_factory=dict)
    options: JsonObject = Field(default_factory=dict)


class ColumnMetadata(StrictBaseModel):
    """Descriptive metadata for a declared table column."""

    model_config = ConfigDict(validate_assignment=True)

    name: str = Field(min_length=1)
    dtype: str
    parameters: JsonObject = Field(default_factory=dict)
    unique: bool = False
    coerce: bool = False
    checks: list[CheckMetadata] = Field(default_factory=list)
    unit: ColumnUnit
    description: str = Field(min_length=1)
    nullable: bool = False


class OutputMetadata(StrictBaseModel):
    """Ordinary or spatial table metadata, independent of row identity."""

    kind: Literal["table"] = "table"
    meaning: str | None = Field(default=None, min_length=1)
    granularity: str | None = Field(default=None, min_length=1)
    columns: list[ColumnMetadata]
    geometry: str | None = Field(default=None, min_length=1)

    checks: list[CheckMetadata] = Field(default_factory=list)
    ordered: bool = False
    coerce: bool = False
    unique: str | list[str] | list[list[str]] | None = None

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        """Require unique attribute and geometry names.

        Returns:
            The validated declaration.

        Raises:
            ValueError: If column names repeat.
        """
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            msg = "Column names must be unique."
            raise ValueError(msg)
        geometry_columns = [
            column.name for column in self.columns if column.dtype == "geometry"
        ]
        if geometry_columns != ([] if self.geometry is None else [self.geometry]):
            msg = "Geometry must identify the single declared geometry column."
            raise ValueError(msg)
        return self


class LocationOutputMetadata(StrictBaseModel):
    """Identity and shared column/check metadata for a location contract."""

    id_column: str
    output: OutputMetadata


def forwarding_schema(target_description: OutputMetadata) -> OutputMetadata:
    """Describe target records expanded by immediate source and target pair.

    Returns:
        A detached schema with composite pair identity.
    """
    description = target_description.model_copy(deep=True)
    # Arbitrary producer checks apply to authoritative records, before expansion.
    description.checks = []
    description.unique = ["source_location_id", "location_id"]
    for column in description.columns:
        column.unique = False
        column.checks = []
    description.columns.insert(
        0,
        ColumnMetadata(
            name="source_location_id",
            dtype="str",
            unit=None,
            description="Immediate source location ID",
            nullable=False,
        ),
    )
    return description
