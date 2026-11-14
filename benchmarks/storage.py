"""Native ownership and relational conversion costs."""

from typing import ClassVar

from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.data.results import serialize_outputs
from lyra.runtime.native_inputs import copy_frame
from lyra.sdk.storage_schema import prepare_native
from test_support.storage_workloads import CASES, storage_frame
from test_support.workloads import schema


class StorageSuite:
    """Measure native values at supported storage boundaries."""

    version = "3"
    params: ClassVar[list[str]] = [
        "mixed-small",
        "mixed-large",
        "polygon-small",
        "polygon-large",
        *CASES,
    ]
    param_names: ClassVar[list[str]] = ["case"]

    def setup(self, case: str) -> None:
        """Compile and validate outside timing."""
        frame = storage_frame(case)
        self.frame, self.description = prepare_native(frame, schema(frame))
        self.rows = rows_from_validated(self.frame, self.description)

    def time_ownership(self, _case: str) -> None:
        """Copy native values and metadata."""
        copy_frame(self.frame)

    def time_to_rows(self, _case: str) -> None:
        """Convert typed columns to SQL cells."""
        rows_from_validated(self.frame, self.description)

    def time_from_rows(self, _case: str) -> None:
        """Reconstruct owned native columns."""
        frame_from_rows(self.rows, self.description)

    def time_roundtrip(self, _case: str) -> None:
        """Convert and reconstruct supported logical values."""
        frame_from_rows(
            rows_from_validated(self.frame, self.description), self.description
        )

    def peakmem_roundtrip(self, case: str) -> None:
        """Measure process peak for relational conversion."""
        self.time_roundtrip(case)

    def time_serialize(self, _case: str) -> None:
        """Serialize at the public transport boundary."""
        serialize_outputs("root", {"root": self.frame})
