"""Serializable provider registrations captured without acquisition."""

from typing import Self

from pydantic import Field, model_validator

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
    description: str | None = None
    query_schema: ProviderQuerySchema | None = None
    query_capable: bool = False

    @property
    def id(self) -> str:
        """Public provider ID."""
        return self.contract.family.collection

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        """Return a declaration whose public provider ID agrees with its scope.

        Raises:
            ValueError: If the contract does not retain its provider identity.
        """
        scope = self.contract.provider_scope
        if self.query_capable != (self.query_schema is not None):
            message = "Query capability requires a declared query schema"
            raise ValueError(message)
        if scope is None or scope.provider_id != self.id:
            message = "Provider contract must retain its declared provider scope"
            raise ValueError(message)
        return self
