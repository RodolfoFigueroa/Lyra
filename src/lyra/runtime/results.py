"""Caller-owned tables and descriptions from one local execution."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lyra.contracts.metric_scope import OutputScope
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject

if TYPE_CHECKING:
    from lyra.data.results import NativeOutputs


class RunOutputMetadata(StrictBaseModel):
    """Declared table structure and captured ownership of a named output."""

    scope: OutputScope
    table_schema: OutputMetadata
    owner_scope: ProviderScope | None


class RunMetadata(StrictBaseModel):
    """Effective root parameters and output descriptions for one execution."""

    metric_name: str
    parameters: JsonObject
    outputs: dict[str, RunOutputMetadata]


@dataclass
class RunResult:
    """Native named tables and metadata owned by the caller.

    Fields use ordinary access; callers can explicitly copy values when needed.
    """

    tables: "NativeOutputs"
    metadata: RunMetadata
