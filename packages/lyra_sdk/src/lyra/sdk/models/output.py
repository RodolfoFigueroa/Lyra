"""Generated descriptions of executable output schemas."""

from typing import Annotated, Literal, Self

from lyra.sdk.dependencies import Input
from lyra.sdk.models.location_payload import CollectionName, UnitType
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.types import JsonObject
from lyra.sdk.units import Unit
from pydantic import Field, model_validator


class CheckMetadata(StrictBaseModel):
    """Descriptive check information, never executable validation."""

    name: str
    description: str | None = None
    kind: Literal["builtin", "python"]
    parameters: JsonObject = Field(default_factory=dict)
    options: JsonObject = Field(default_factory=dict)


class IndexLevelMetadata(StrictBaseModel):
    """One declared native index level."""

    name: str | int | None = None
    dtype: str | None = None
    parameters: JsonObject = Field(default_factory=dict)
    unique: bool = False
    coerce: bool = False
    nullable: bool = False
    checks: list[CheckMetadata] = Field(default_factory=list)


class IndexMetadata(StrictBaseModel):
    """Index levels and their container constraints."""

    kind: Literal["index", "multiindex"]
    levels: list[IndexLevelMetadata]
    ordered: bool = True
    strict: bool = False
    coerce: bool = False
    unique: str | list[str] | None = None


class ColumnMetadata(StrictBaseModel):
    """Descriptive metadata for a declared table column."""

    name: str = Field(min_length=1)
    dtype: str
    parameters: JsonObject = Field(default_factory=dict)
    unique: bool = False
    coerce: bool = False
    checks: list[CheckMetadata] = Field(default_factory=list)
    unit: Unit | None
    description: str = Field(min_length=1)
    nullable: bool = False


class OutputMetadata(StrictBaseModel):
    """Ordinary or spatial table metadata, independent of row identity."""

    kind: Literal["table"] = "table"
    columns: list[ColumnMetadata]
    geometry: str | None = Field(default=None, min_length=1)

    checks: list[CheckMetadata] = Field(default_factory=list)
    index: IndexMetadata | None = None
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


class FixedLocationScope(StrictBaseModel):
    """An operation's declared entity scope."""

    kind: Literal["fixed"] = "fixed"
    collection: CollectionName
    unit_type: UnitType


class LocationOperationOutputMetadata(LocationOutputMetadata):
    """An operation schema and its explicit scope declaration."""

    scope: Annotated[FixedLocationScope | Input, Field(discriminator="kind")]
