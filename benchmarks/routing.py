"""Canonical routing compilation across graph sizes and shapes."""

from contextlib import suppress
from typing import ClassVar

from lyra.runtime.planning.catalog import (
    AmbiguousRoutesError,
    CanonicalEdge,
    compile_routes,
)
from test_support.location_workloads import Provider


class RoutingSuite:
    """Compile routes including cyclic and exponentially branching graphs."""

    version = "1"
    params: ClassVar[list[list[int] | list[str]]] = [
        [10, 100],
        ["chain", "cycle", "layered", "preferred"],
    ]
    param_names: ClassVar[list[str]] = ["contracts", "shape"]

    def setup(self, contracts: int, shape: str) -> None:
        """Build concrete contracts and eligible edges outside measurement."""
        bindings = [
            Provider.contracts[0].model_copy(update={"source": f"source_{i}"})
            for i in range(contracts)
        ]
        pairs = [(i, i + 1) for i in range(contracts - 1)]
        if shape == "cycle":
            pairs.append((contracts - 1, 0))
        elif shape == "layered":
            pairs = [
                (i + a, i + 2 + b)
                for i in range(0, contracts - 2, 2)
                for a in range(2)
                for b in range(2)
            ]
        self.edges = tuple(
            CanonicalEdge(f"op_{i}", "source", bindings[a], bindings[b])
            for i, (a, b) in enumerate(pairs)
        )

        self.preferences = ()
        if shape == "preferred":
            self.preferences = tuple(
                tuple(f"op_{k}" for k in range(i, j))
                for i in range(contracts)
                for j in range(i + 1, contracts)
            )

    def time_compile(self, _contracts: int, _shape: str) -> None:
        """Measure route computation and bounded ambiguity diagnostics."""
        with suppress(AmbiguousRoutesError):
            compile_routes(self.edges, preferences=self.preferences)

    def peakmem_compile(self, contracts: int, shape: str) -> None:
        """Measure process peak memory during route computation."""
        self.time_compile(contracts, shape)
