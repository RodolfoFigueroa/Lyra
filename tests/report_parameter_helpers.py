"""Parameter models for report forwarding fixtures."""

from pydantic import Field

from lyra.sdk.parameters import MetricParameters


class RootParameters(MetricParameters):
    search_radius: int = Field(5, description="Search radius")
    alternate: int = Field(5, description="Alternate radius")
    radius: int = Field(99, description="Overridden matching name")
    options: dict[str, list[int | None]] = Field(
        default_factory=lambda: {"items": [1, None]}, description="Options"
    )
    tags: list[str] = Field(default_factory=list, description="Tags")
    label: str | None = Field(None, description="Label")
    unrelated: bool = Field(default=True, description="Root only")


class MiddleParameters(MetricParameters):
    radius: int = Field(5, description="Radius")
    options: dict[str, list[int | None]] = Field(
        default_factory=dict, description="Options"
    )
    tags: list[str] = Field(default_factory=list, description="Tags")
    label: str | None = Field(None, description="Label")
    mode: str = Field("default", description="Receiving default")


class LeafParameters(MetricParameters):
    distance: int = Field(5, description="Distance")
