"""Compact accepted context and observations, without retaining execution data."""

from dataclasses import dataclass
from typing import Literal

from lyra.sdk.preparation import PreparedExecution


@dataclass(frozen=True)
class NativeInputSummary:
    """Ordered native content identity without its tables or geometries."""

    name: str
    count: int
    fingerprint: str


@dataclass(frozen=True)
class InvocationObservation:
    """An actual executor boundary visited by this run."""

    node: str
    invocation: str
    path: tuple[str, ...]
    outcome: Literal["started", "completed", "cache_hit", "shared_reuse"]


@dataclass(frozen=True)
class ExecutionProvenance:
    """Accepted plan and current-run observations, also retained on failure."""

    accepted: PreparedExecution | None
    native_inputs: tuple[NativeInputSummary, ...]
    observations: tuple[InvocationObservation, ...]
    succeeded: bool
