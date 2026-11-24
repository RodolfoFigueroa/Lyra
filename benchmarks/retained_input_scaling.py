"""Shared-input, correspondence, and read-memory comparisons."""

from typing import ClassVar

from test_support.retained_benchmarks import (
    COLD_CASES,
    LARGE_CASES,
    MEMORY_CASES,
    MIXED_CASES,
    SMALL_CASES,
    SMALL_MIXED_CASES,
    Sample,
    Seed,
    large_seeds,
    small_seeds,
)


class SmallInputSuite(Sample):
    """Scale shared inputs and correspondences at one hundred partitions."""

    params: ClassVar[list[str]] = [case.name for case in SMALL_CASES]
    setup_cache = staticmethod(small_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Copy a completed small fixture outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute a complete cached request or describe its retained inputs."""
        self.execute()


class LargeInputSuite(Sample):
    """Measure shared inputs and correspondences at one thousand partitions."""

    params: ClassVar[list[str]] = [case.name for case in LARGE_CASES]
    setup_cache = staticmethod(large_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Copy a completed large fixture outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Consume the selected complete operation including read-store lifetime."""
        self.execute()


class MixedInputSuite(Sample):
    """Publish requests whose first ninety percent of sources are cached."""

    params: ClassVar[list[str]] = [case.name for case in MIXED_CASES]
    setup_cache = staticmethod(large_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Copy a ninety-percent-warm store outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute the complete mixed request under its declared cache policy."""
        self.execute()


class SmallMixedInputSuite(Sample):
    """Publish requests whose first ninety percent of sources are cached."""

    params: ClassVar[list[str]] = [case.name for case in SMALL_MIXED_CASES]
    setup_cache = staticmethod(small_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Copy a ninety-percent-warm store outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute the complete mixed request under its declared cache policy."""
        self.execute()


class ColdInputSuite(Sample):
    """Publish shared references and dense correspondences into an empty store."""

    params: ClassVar[list[str]] = [case.name for case in COLD_CASES]

    def setup(self, case: str) -> None:
        """Prepare definitions and inputs without acquiring a database."""
        self.begin(case)

    def time_operation(self, _case: str) -> None:
        """Execute one complete cold request with normal storage durability."""
        self.execute()


class InputMemorySuite(Sample):
    """Measure process peaks without constructing retained fixtures in the process."""

    params: ClassVar[list[str]] = [case.name for case in MEMORY_CASES]
    setup_cache = staticmethod(large_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Copy a closed seed prepared by ASV in a separate process."""
        self.begin(case, seeds)

    def peakmem_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Include imports and lightweight setup; exclude seed creation and teardown."""
        self.execute()
