"""Typed data validation, fingerprints, artifacts, and result serialization."""

from typing import ClassVar

from lyra.data import ipc_codec
from lyra.data.results import serialize_outputs
from lyra.data.typed_storage import Snapshot
from lyra.sdk.storage_schema import arrow_schema
from test_support.storage_workloads import CASES, storage_frame
from test_support.workloads import schema


class StorageSuite:
    """Measure mixed scalar and spatial data through production boundaries."""

    version = "2"
    params: ClassVar[list[str]] = [
        "mixed-small",
        "mixed-large",
        "polygon-small",
        "polygon-large",
        *CASES,
    ]
    param_names: ClassVar[list[str]] = ["case"]

    def setup(self, case: str) -> None:
        """Prepare validated data and warm artifact identities."""
        self.frame = storage_frame(case)
        self.schema = arrow_schema(schema(self.frame))
        self.snapshot = Snapshot.from_frame(self.frame, self.schema)
        self.encoded = ipc_codec.encode(self.snapshot)

    def time_construct(self, _case: str) -> None:
        """Construct an owned typed snapshot."""
        Snapshot.from_frame(self.frame, self.schema)

    def time_export(self, _case: str) -> None:
        """Materialize a complete independent frame."""
        self.snapshot.to_frame()

    def time_roundtrip(self, _case: str) -> None:
        """Construct, fingerprint, encode, decode, and materialize fresh contents."""
        snapshot = Snapshot.from_frame(self.frame, self.schema)
        ipc_codec.decode(ipc_codec.encode(snapshot)).to_frame()

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

    version = "2"
    params: ClassVar[list[str]] = ["mixed", "polygon", *CASES]
    number = 1
    warmup_time = 0

    def setup(self, kind: str) -> None:
        """Create a fresh snapshot for each timed invocation."""
        table = storage_frame(kind if kind in CASES else f"{kind}-large")
        self.snapshot = Snapshot.from_frame(table, arrow_schema(schema(table)))

    def time_first(self, _kind: str) -> None:
        """Compute content identities once."""
        _ = self.snapshot.fingerprints

    def peakmem_first(self, _kind: str) -> None:
        """Measure total process memory during fingerprinting."""
        _ = self.snapshot.fingerprints


class StorageMemorySuite:
    """Whole-process peaks for representative scalar, point, and complex data."""

    version = "1"
    params: ClassVar[list[list[str]]] = [
        ["typed-scalars", "varied-points", "complex-large"],
        ["construct", "encode", "decode", "export", "roundtrip"],
    ]
    param_names: ClassVar[list[str]] = ["case", "operation"]

    def setup(self, case: str, operation: str) -> None:
        """Prepare only the state required by the measured operation."""
        self.frame = storage_frame(case)
        self.schema = arrow_schema(schema(self.frame))
        if operation in {"encode", "decode", "export"}:
            self.snapshot = Snapshot.from_frame(self.frame, self.schema)
            del self.frame
        if operation in {"encode", "decode"}:
            _ = self.snapshot.fingerprints
        if operation == "decode":
            self.encoded = ipc_codec.encode(self.snapshot)
            del self.snapshot

    def peakmem_operation(self, _case: str, operation: str) -> None:
        """Include imported libraries and fixture preparation in the peak."""
        if operation == "construct":
            Snapshot.from_frame(self.frame, self.schema)
        elif operation == "encode":
            ipc_codec.encode(self.snapshot)
        elif operation == "decode":
            ipc_codec.decode(self.encoded)
        elif operation == "export":
            self.snapshot.to_frame()
        else:
            snapshot = Snapshot.from_frame(self.frame, self.schema)
            ipc_codec.decode(ipc_codec.encode(snapshot)).to_frame()
