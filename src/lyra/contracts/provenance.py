"""Compact accepted context and observations, without retaining execution data."""

from dataclasses import dataclass as plain_dataclass
from typing import Literal

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from lyra.contracts.accepted import PreparedExecution


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class NativeInputSummary:
    """Input names and row counts without retaining their contents."""

    name: str
    count: int


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class InvocationObservation:
    """An actual executor boundary visited by this run.

    Partial hits accompany execution observations; complete hits need no handler.
    Bypassed uses have no visit, so another consumer's shared work stays visible.
    """

    node: str
    invocation: str
    path: tuple[str, ...]
    outcome: Literal["started", "completed", "cache_hit", "partial_hit", "shared_reuse"]


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
