"""Caller-owned tables and descriptions from one local execution."""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.contracts.reports import ExecutionReport
from lyra.contracts.reports import ReportOutput as RunOutputMetadata
from lyra.contracts.results import ExecutionMetadata, ResultDescription

if TYPE_CHECKING:
    from lyra.data.results import NativeOutputs
    from lyra.runtime.result_store import ResultStore


__all__ = [
    "ExecutionMetadata",
    "ResultStore",
    "RunMetadata",
    "RunOutputMetadata",
    "RunResult",
]


class RunMetadata(ResultDescription):
    """Retained local execution metadata and its permanent store address."""

    store_path: Path


@dataclass
class RunResult:
    """Native named tables and metadata owned by the caller.

    Fields use ordinary access; callers can explicitly copy values when needed.
    """

    tables: "NativeOutputs"
    metadata: RunMetadata
    report: ExecutionReport


def __getattr__(name: str) -> object:
    """Load retained access only when requested, keeping metadata imports offline.

    Returns:
        The public ResultStore facade.

    Raises:
        AttributeError: If the requested name is not exported.
    """
    if name == "ResultStore":
        return import_module("lyra.runtime.result_store").ResultStore
    raise AttributeError(name)
