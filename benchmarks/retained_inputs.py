"""Retained-input invalidation, retrieval, memory, and storage comparisons."""

from typing import ClassVar

from test_support.retained_benchmarks import (
    CASES,
    COLD_MEMORY_CASES,
    COMBINED_CASES,
    EXTRA_MEMORY_CASES,
    INVALIDATION_CASES,
    LARGE_STORAGE_CASES,
    READ_CASES,
    SMALL_MIXED_CASES,
    SMALL_STORAGE_CASES,
    Sample,
    Seed,
    combined_seeds,
    large_seeds,
    small_seeds,
)


class ReadSuite(Sample):
    """Consume complete native frames and Arrow streams."""

    params: ClassVar[list[str]] = [case.name for case in READ_CASES]
    setup_cache = staticmethod(large_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute one complete operation."""
        self.execute()


class InvalidationSuite(Sample):
    """Execute after provider revision or reference selection changes."""

    params: ClassVar[list[str]] = [case.name for case in INVALIDATION_CASES]
    setup_cache = staticmethod(small_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute one complete operation."""
        self.execute()


class CombinedSuite(Sample):
    """Combine dense pairs with independent references at 100 sources."""

    params: ClassVar[list[str]] = [case.name for case in COMBINED_CASES]
    setup_cache = staticmethod(combined_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case, seeds)

    def time_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute one complete operation."""
        self.execute()


class ColdMemorySuite(Sample):
    """Measure an empty-store execution peak without prepared seeds."""

    params: ClassVar[list[str]] = [case.name for case in COLD_MEMORY_CASES]

    def setup(self, case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case)

    def peakmem_operation(self, _case: str) -> None:
        """Execute before ASV captures the process peak."""
        self.execute()


class ReadWriteMemorySuite(Sample):
    """Measure streaming peaks without seed creation."""

    params: ClassVar[list[str]] = [case.name for case in EXTRA_MEMORY_CASES]
    setup_cache = staticmethod(large_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case, seeds)

    def peakmem_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute before ASV captures the process peak."""
        self.execute()


class MixedMemorySuite(Sample):
    """Measure mixed execution peaks without seed creation."""

    params: ClassVar[list[str]] = [case.name for case in SMALL_MIXED_CASES]
    setup_cache = staticmethod(small_seeds)

    def setup(self, seeds: dict[str, Seed], case: str) -> None:
        """Prepare an isolated sample outside measurement."""
        self.begin(case, seeds)

    def peakmem_operation(self, _seeds: dict[str, Seed], _case: str) -> None:
        """Execute before ASV captures the process peak."""
        self.execute()


class SmallStorageSuite:
    """Track total file bytes and growth at 100 sources."""

    version = "1"
    timeout = 1200
    unit = "bytes"
    params: ClassVar[list[str]] = [case.name for case in SMALL_STORAGE_CASES]
    param_names: ClassVar[list[str]] = ["case"]
    setup_cache = staticmethod(small_seeds)

    @staticmethod
    def track_total_bytes(seeds: dict[str, Seed], case: str) -> int:
        """Return the prepared database file size."""
        selected = CASES[case]
        return seeds[selected.fixture.name].sizes[selected.operation][1]

    @staticmethod
    def track_growth_bytes(seeds: dict[str, Seed], case: str) -> int:
        """Return growth from the same prepared publication."""
        selected = CASES[case]
        before, after = seeds[selected.fixture.name].sizes[selected.operation]
        return after - before


class LargeStorageSuite:
    """Track total file bytes and growth at 1,000 sources."""

    version = "1"
    timeout = 1200
    unit = "bytes"
    params: ClassVar[list[str]] = [case.name for case in LARGE_STORAGE_CASES]
    param_names: ClassVar[list[str]] = ["case"]
    setup_cache = staticmethod(large_seeds)

    @staticmethod
    def track_total_bytes(seeds: dict[str, Seed], case: str) -> int:
        """Return the prepared database file size."""
        selected = CASES[case]
        return seeds[selected.fixture.name].sizes[selected.operation][1]

    @staticmethod
    def track_growth_bytes(seeds: dict[str, Seed], case: str) -> int:
        """Return growth from the same prepared publication."""
        selected = CASES[case]
        before, after = seeds[selected.fixture.name].sizes[selected.operation]
        return after - before
