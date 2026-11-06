"""Runtime context supplied to node implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.data.locations import Locations


class RunContext(Protocol):
    """Runtime services and durable reporting hooks provided to a node invocation."""

    @property
    def job_id(self) -> str:
        """The stable identifier of the job being executed."""
        ...

    @property
    def node(self) -> str:
        """The globally unique name of the node being executed."""
        ...

    @property
    def logger(self) -> logging.Logger | logging.LoggerAdapter:
        """The logger for diagnostic, non-client-facing run details."""
        ...

    @property
    def temp_dir(self) -> Path:
        """The job-scoped directory for internal scratch files."""
        ...

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Publish quantitative progress for the current stage.

        Estimates, stages, and units may change between snapshots. Rapid
        updates are coalesced to the latest value.

        Args:
            stage: Stable name of the current unit of work.
            current: Non-negative amount completed in this stage.
            total: Positive amount that completes the stage, when known.
            unit: Human-readable unit for ``current`` and ``total``.
            message: Optional concise client-facing description of this update.

        Raises:
            ValueError: If the values are invalid.

        """
        ...


class LocationContext(RunContext, Protocol):
    """Reporting services and scoped location resolution for location operations."""

    def resolve_locations(
        self,
        collection: str,
        unit_type: str,
        ids: list[str],
        *,
        input_name: str = "locations",
    ) -> Locations:
        """Resolve exact IDs in first-occurrence order; empty IDs retain scope.

        Duplicate requests resolve once. Empty lookups validate the declared
        scope and schema without acquiring the provider. Resolution shares
        the execution resource scope used for initial public selections.
        """
        ...
