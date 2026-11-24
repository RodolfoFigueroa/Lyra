"""Serializable provider registrations captured without acquisition."""

from typing import Self

from pydantic import Field, StrictBool, model_validator

from lyra.contracts.locations import LocationContract
from lyra.contracts.strict import StrictBaseModel


class QueryColumn(StrictBaseModel):
    """Serializable logical query type for one registered column."""

    name: str
    dtype: str
    nullable: bool


class ProviderQuerySchema(StrictBaseModel):
    """Detached query shape, including the authoritative geometry CRS."""

    columns: list[QueryColumn]
    crs: str


class ProviderDeclaration(StrictBaseModel):
    """A provider's lifecycle binding and complete scoped record contract."""

    resource: str = Field(min_length=1)
    contract: LocationContract
    cache: StrictBool = False
    description: str | None = None
    query_schema: ProviderQuerySchema | None = None
    query_capable: bool = False
    membership_capable: bool = False

    @property
    def id(self) -> str:
        """Public provider ID."""
        return self.contract.provider_scope.provider_id

    @model_validator(mode="after")
    def validate_query_schema(self) -> Self:
        """Return a declaration with consistent query capability and schema.

        Raises:
            ValueError: If the declared capability and schema disagree.
        """
        if self.query_capable != (self.query_schema is not None):
            message = "Query capability requires a declared query schema"
            raise ValueError(message)
        return self
