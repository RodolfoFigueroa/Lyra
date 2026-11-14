"""Native ownership, conversion, and graph investigation workloads."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pandas as pd

from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.runtime.native_inputs import copy_frame
from lyra.sdk.storage_schema import prepare_native
from tests.frame_assertions import assert_logical_equal
from tests.location_graph import workload_inputs
from tests.storage_cases import Fixture, graph


def require(condition: object, message: str) -> None:
    """Enforce workload correctness outside measurements.

    Raises:
        AssertionError: When the workload differs from its expected result.
    """
    if not condition:
        raise AssertionError(message)


@dataclass
class Operation:
    """Action with untimed behavioral verification."""

    action: Callable[[], object]
    verify: Callable[[object], None]
    metadata: dict = field(default_factory=dict)


class Work:
    """Owned native inputs with declared relational conversions."""

    def __init__(self, data: Fixture) -> None:
        """Prepare values and compiled layout outside timing."""
        self.data = data
        self.expected = copy_frame(data.frame)
        self.frame, self.description = prepare_native(data.frame, data.schema)

    def check_frame(self, result: object) -> None:
        """Verify logical values and geometry after conversion."""
        assert_logical_equal(cast("pd.DataFrame", result), self.expected)

    def operations(self) -> list[str]:
        """Return native and graph boundaries."""
        return ["ownership", "roundtrip"] + [
            f"graph:{shape}:{mode}"
            for shape in (
                ("chain", "fanout", "shared")
                if self.data.case.kind == "rich"
                else ("shared",)
            )
            for mode in ("off", "cold", "full", "intermediate")
        ]

    def prepare(self, name: str, directory: Path) -> Operation:
        """Prepare the selected measured boundary.

        Raises:
            ValueError: For an unsupported operation.
        """
        if name.startswith("graph:"):
            _, shape, mode = name.split(":")
            return self.prepare_graph(shape, mode, directory)
        if name == "ownership":
            return Operation(lambda: copy_frame(self.frame), self.check_frame)
        if name == "roundtrip":
            return Operation(
                lambda: frame_from_rows(
                    rows_from_validated(self.frame, self.description), self.description
                ),
                self.check_frame,
            )
        raise ValueError(name)

    def prepare_graph(self, shape: str, mode: str, directory: Path) -> Operation:
        """Return a seeded graph action with untimed handler checks."""
        runner, calls = graph(
            self.data, shape, directory, cache="off" if mode == "off" else "full"
        )
        if mode in {"full", "intermediate"}:
            runner.run(
                "root",
                inputs=workload_inputs(self.data.value)
                if self.data.value is not None
                else {},
            )
            if mode == "intermediate":
                runner, calls = graph(
                    self.data, shape, directory, consumer_revision="2"
                )
            calls.clear()
        metadata = {"handlers": calls}

        def verify(result: object) -> None:
            outputs = cast("dict", result)
            assert_logical_equal(
                outputs["root"], pd.DataFrame({"count": [2 * self.data.case.rows]})
            )
            expected = (
                []
                if mode == "full"
                else ["left", "root"]
                if shape == "chain"
                else ["left", "right", "root"]
            )
            if self.data.value is not None:
                expected = ["provider", *expected]
            if mode in {"full", "intermediate"}:
                require(calls == expected, f"Unexpected cache handlers: {calls}")
            else:
                require(
                    calls.count("source") == 1 and calls.count("root") == 1,
                    f"Unexpected fresh handlers: {calls}",
                )
            metadata["artifact_directory_bytes"] = (
                sum(
                    member.stat().st_size
                    for member in directory.rglob("*")
                    if member.is_file()
                )
                if directory.exists()
                else 0
            )

        return Operation(
            lambda: (
                runner.run(
                    "root",
                    inputs=workload_inputs(self.data.value)
                    if self.data.value is not None
                    else {},
                ).tables
            ),
            verify,
            metadata,
        )
