"""Construction and retained size of compact execution provenance."""

from typing import cast

from pydantic import TypeAdapter

from lyra.contracts.provenance import ExecutionProvenance
from test_support.canonical_execution import canonical_workload


class ProvenanceSuite:
    """Measure records independently of source acquisition and handlers."""

    def setup(self) -> None:
        """Capture representative accepted metadata and native summaries."""
        work = canonical_workload("routed", 1000, 2)
        work.executor.execute(work.prepared)
        self.record = cast("ExecutionProvenance", work.executor.provenance)

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
