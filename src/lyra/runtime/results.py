"""Caller-owned tables and descriptions from one local execution."""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.contracts.metric_scope import OutputScope
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject

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


class RunOutputMetadata(StrictBaseModel):
    """Declared table structure and captured ownership of a named output."""

    scope: OutputScope
    table_schema: OutputMetadata
    owner_scope: ProviderScope | None


class ExecutionMetadata(StrictBaseModel):
    """Effective root parameters and output descriptions for one execution."""

    metric_name: str
    parameters: JsonObject
    outputs: dict[str, RunOutputMetadata]


class RunMetadata(ExecutionMetadata):
    """Retained local execution metadata and its permanent store address."""

    store_path: Path
    result_ref: str


@dataclass
class RunResult:
    """Native named tables and metadata owned by the caller.

    Fields use ordinary access; callers can explicitly copy values when needed.
    """

    tables: "NativeOutputs"
    metadata: RunMetadata


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
