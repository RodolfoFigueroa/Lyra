"""Construction and retained size of compact execution provenance."""

from typing import cast

from lyra.sdk.provenance import ExecutionProvenance
from pydantic import TypeAdapter

from benchmarks.execution import CanonicalExecutionSuite


class ProvenanceSuite:
    """Measure records independently of source acquisition and handlers."""

    def setup(self) -> None:
        """Capture representative accepted metadata and native summaries."""
        suite = CanonicalExecutionSuite()
        suite.setup("routed", 1000, 2)
        suite.executor.execute(suite.prepared)
        self.record = cast("ExecutionProvenance", suite.executor.provenance)

    def time_construct(self) -> None:
        """Construct an immutable current-run record from retained components."""
        ExecutionProvenance(
            self.record.accepted,
            self.record.execution,
        )

    def track_retained_bytes(self) -> int:
        """Return serialized retained context size.

        Returns:
            Bytes including accepted metadata and observations.
        """
        return len(TypeAdapter(ExecutionProvenance).dump_json(self.record))
