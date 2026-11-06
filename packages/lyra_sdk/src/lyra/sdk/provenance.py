"""Compact accepted context and observations, without retaining execution data."""

from dataclasses import dataclass as plain_dataclass
from typing import Literal

from lyra.sdk.accepted import PreparedExecution
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class NativeInputSummary:
    """Ordered native content identity without its tables or geometries."""

    name: str
    count: int
    fingerprint: str


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class InvocationObservation:
    """An actual executor boundary visited by this run."""

    node: str
    invocation: str
    path: tuple[str, ...]
    outcome: Literal["started", "completed", "cache_hit", "shared_reuse"]


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class ExecutionObservations:
    """Current-run observations, without acceptance or execution data."""

    native_inputs: tuple[NativeInputSummary, ...]
    observations: tuple[InvocationObservation, ...]
    succeeded: bool


@plain_dataclass(frozen=True)
class ExecutionProvenance:
    """Local acceptance and observations, also retained on failure."""

    accepted: PreparedExecution | None
    execution: ExecutionObservations
