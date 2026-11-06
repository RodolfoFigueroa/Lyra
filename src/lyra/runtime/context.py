"""Runtime adapters for plugin execution contexts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.data.locations import Locations
    from lyra.runtime.locations import LocationResolver
    from lyra.sdk.context import RunContext


class LocationContextAdapter:
    """Compose local or worker reporting with a caller-owned location resolver."""

    def __init__(self, context: RunContext, resolver: LocationResolver) -> None:
        """Borrow reporting services and the resolver without owning their lifetime."""
        self._context = context
        self._resolver = resolver

    @property
    def job_id(self) -> str:
        """The reporting context's job identity."""
        return self._context.job_id

    @property
    def node(self) -> str:
        """The reporting context's receiving node identity."""
        return self._context.node

    @property
    def logger(self) -> logging.Logger | logging.LoggerAdapter:
        """The original diagnostic logger."""
        return self._context.logger

    @property
    def temp_dir(self) -> Path:
        """The original job-scoped scratch directory."""
        return self._context.temp_dir

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Forward progress to the reporting context with its validation intact."""
        self._context.report_progress(
            stage=stage,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )

    def resolve_locations(
        self,
        collection: str,
        unit_type: str,
        ids: list[str],
        *,
        input_name: str = "locations",
    ) -> Locations:
        """Return validated locations with receiving input and node diagnostics."""
        return self._resolver.resolve_locations(
            collection,
            unit_type,
            ids,
            input_name=input_name,
            node=self.node,
        )
