"""Typed data validation, fingerprints, artifacts, and result serialization."""

from typing import ClassVar

from lyra.sdk import ipc_codec
from lyra.sdk.results import serialize_outputs
from lyra.sdk.typed_storage import Snapshot

from benchmarks.workloads import frame, schema


class StorageSuite:
    """Measure mixed scalar and spatial data through production boundaries."""

    version = "1"
    params: ClassVar[list[str]] = [
        "mixed-small",
        "mixed-large",
        "polygon-small",
        "polygon-large",
    ]
    param_names: ClassVar[list[str]] = ["case"]

    def setup(self, case: str) -> None:
        """Prepare validated data and warm artifact identities."""
        kind, size = case.split("-")
        rows = 1000 if size == "small" else 10000
        self.frame = frame(rows, kind)
        self.schema = schema(self.frame)
        self.snapshot = Snapshot.from_frame(self.frame, self.schema)
        self.encoded = ipc_codec.encode(self.snapshot)

    def time_construct(self, _case: str) -> None:
        """Construct an owned typed snapshot."""
        Snapshot.from_frame(self.frame, self.schema)

    def time_encode(self, _case: str) -> None:
        """Encode with a cached fingerprint."""
        ipc_codec.encode(self.snapshot)

    def time_decode(self, _case: str) -> None:
        """Decode and validate owned buffers."""
        ipc_codec.decode(self.encoded)

    def time_fingerprint_cached(self, _case: str) -> None:
        """Access previously computed immutable identities."""
        _ = self.snapshot.fingerprints

    def time_serialize(self, _case: str) -> None:
        """Produce public JSON-compatible native output data."""
        serialize_outputs("root", {"root": self.frame})

    def track_artifact_bytes(self, _case: str) -> int:
        """Return encoded artifact size."""
        return len(self.encoded)


class FingerprintSuite:
    """Measure first fingerprint computation without accidental warming."""

    version = "1"
    params: ClassVar[list[str]] = ["mixed", "polygon"]
    number = 1
    warmup_time = 0

    def setup(self, kind: str) -> None:
        """Create a fresh snapshot for each timed invocation."""
        table = frame(10000, kind)
        self.snapshot = Snapshot.from_frame(table, schema(table))

    def time_first(self, _kind: str) -> None:
        """Compute content identities once."""
        _ = self.snapshot.fingerprints

    def peakmem_first(self, _kind: str) -> None:
        """Measure total process memory during fingerprinting."""
        _ = self.snapshot.fingerprints
