"""Models for administrative status and control operations."""

from pydantic import Field

from lyra.contracts.strict import StrictBaseModel


class InstalledPluginResponse(StrictBaseModel):
    """Configured installed plugin details."""

    distribution: str = Field(min_length=1)
    version: str | None = None
    enabled: bool


class InstalledPluginListResponse(StrictBaseModel):
    """Collection of configured installed plugins."""

    plugins: list[InstalledPluginResponse]
