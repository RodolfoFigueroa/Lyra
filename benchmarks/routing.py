"""Canonical routing compilation across graph sizes and shapes."""

from typing import ClassVar

from lyra.runtime.planning.catalog import (
    AmbiguousRoutesError,
    compile_live_catalog,
)
from test_support.canonical_routes import route_plugins


class RoutingSuite:
    """Compile routes including cyclic and exponentially branching graphs."""

    version = "2"
    params: ClassVar[list[list[int] | list[str]]] = [
        [10, 100],
        ["chain", "cycle", "layered", "preferred"],
    ]
    param_names: ClassVar[list[str]] = ["contracts", "shape"]

    def setup(self, contracts: int, shape: str) -> None:
        """Build concrete contracts and eligible edges outside measurement."""
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
        edges = {
            f"op_{i}": (f"source_{a}", f"source_{b}") for i, (a, b) in enumerate(pairs)
        }
        preferences = (
            [
                tuple(f"op_{k}" for k in range(i, j))
                for i in range(contracts)
                for j in range(i + 1, contracts)
            ]
            if shape == "preferred"
            else []
        )
        self.definitions = route_plugins(edges, preferences=preferences)
        self.ambiguous = shape == "layered"

    def time_compile(self, _contracts: int, _shape: str) -> None:
        """Compile live routes and check ambiguity diagnostics.

        Raises:
            AmbiguousRoutesError: If a uniquely routed graph has a tie.
            RuntimeError: If a layered graph loses its ambiguity.
        """
        try:
            compile_live_catalog(self.definitions)
        except AmbiguousRoutesError:
            if not self.ambiguous:
                raise
        else:
            if self.ambiguous:
                msg = "Layered graph accepted ambiguous routes"
                raise RuntimeError(msg)

    def peakmem_compile(self, contracts: int, shape: str) -> None:
        """Measure process peak memory during route computation."""
        self.time_compile(contracts, shape)
