"""Adapt only completed-result ownership in Lyra's real graph executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pandas as pd
import shapely
from lyra.sdk.execution import GraphExecutor, RunState
from lyra.sdk.graph import build_graph

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.sdk.arguments import PreparedResolvedInvocation
    from lyra.sdk.context import RunContext
    from lyra.sdk.models.location import Locations
    from lyra.sdk.results import NativeOutputs

    from benchmarks.metric_representation.storage import ResultStore
    from benchmarks.metric_representation.workloads import Workload


def _memory_field(name: str) -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}:"):
            return int(line.split()[1]) / 1024
    msg = "Linux /proc is required for RSS measurements"
    raise RuntimeError(msg)


def rss_mib() -> float:
    """Read Linux resident memory, including native buffers.

    Returns:
        Current resident memory in MiB.
    """
    return _memory_field("VmRSS")


def peak_mib() -> float:
    """Read the address space's high-water mark, including short-lived peaks.

    Returns:
        Peak resident memory in MiB. Unlike getrusage, VmHWM excludes memory
        inherited from the parent's address space before the child exec.
    """
    return _memory_field("VmHWM")


def no_context(node: str) -> RunContext:
    """Reject unexpected resource access in this offline benchmark.

    Raises:
        RuntimeError: Always; none of the handlers needs a context.
    """
    msg = f"Unexpected context request: {node}"
    raise RuntimeError(msg)


@dataclass
class BoundaryExecutor(GraphExecutor):
    """Use normal execution, validation, locks, and Parquet cache publication.

    Only the run's completed-result representation changes. Native temporary
    entries are removed immediately after freezing so Arrow does not secretly
    retain both versions. All variants export at the same consumer boundaries.
    """

    store: ResultStore = field(kw_only=True)
    retained_rss_mib: float = field(default=0.0, init=False)

    def _invoke(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs | Locations:
        key = invocation.identity
        if key not in self.store.entries:
            outputs = super()._invoke(invocation, path, completed, context_factory)
            self.store.put(key, cast("NativeOutputs", outputs))
            completed.pop(key)
            del outputs
        result = self.store.get(key)
        if invocation.node == "root":
            self.retained_rss_mib = rss_mib()
        return result

    def run(self, workload: Workload) -> NativeOutputs:
        """Execute once and release all retained snapshots, including on failure.

        Returns:
            The root's native outputs.
        """
        try:
            return self.execute(workload.plugin.prepare_invocation("root", {}))
        finally:
            self.store.entries.clear()


def make_executor(workload: Workload, store: ResultStore) -> BoundaryExecutor:
    """Build the real SDK graph with an experimental result ownership adapter.

    Returns:
        An executor with no external resources.
    """
    plugins = {"benchmark": workload.plugin}
    graph = build_graph({"benchmark": workload.plugin.node_manifests})
    return BoundaryExecutor(graph, plugins, no_context, store=store)


def assert_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Check exact values, pandas metadata, CRS, and geometry encoding.

    Raises:
        AssertionError: If any native table contract differs.
    """
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    if hasattr(expected, "geometry"):
        if actual.crs != expected.crs:
            msg = "CRS changed"
            raise AssertionError(msg)
        left = shapely.to_wkb(actual.geometry.to_numpy())
        right = shapely.to_wkb(expected.geometry.to_numpy())
        if not (left == right).all():
            msg = "Geometry encoding changed"
            raise AssertionError(msg)
