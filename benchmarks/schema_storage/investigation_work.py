"""Prepare isolated operations and verify results outside measured intervals."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import geopandas as gpd
import pandas as pd
from lyra.sdk import (
    Locations,
    arrow_conversion,
    content_fingerprints,
    geometry_shapely,
    ipc_codec,
)
from lyra.sdk.arrow_ownership import own
from lyra.sdk.cache import TableCache
from lyra.sdk.models.location import location_snapshot
from lyra.sdk.typed_storage import Snapshot

from benchmarks.schema_storage.investigation_cases import Fixture, graph
from benchmarks.schema_storage.run import assert_equal

if TYPE_CHECKING:
    import pyarrow as pa


def require(condition: object, message: str) -> None:
    """Enforce a correctness gate even when Python assertions are disabled.

    Raises:
        AssertionError: If the gate fails.
    """
    if not condition:
        raise AssertionError(message)


@dataclass
class Operation:
    """An action with untimed verification and diagnostic metadata."""

    action: Callable[[], object]
    verify: Callable[[object], None]
    metadata: dict = field(default_factory=dict)


class Work:
    """Prepared inputs, preserving an independent expected frame."""

    def __init__(self, data: Fixture) -> None:
        """Prepare a baseline artifact and fully verify the fixture."""
        self.data = data
        self.expected = data.frame.copy(deep=True)
        self.snapshot = (
            location_snapshot(data.value)
            if data.value is not None
            else Snapshot.from_frame(data.frame, data.schema)
        )
        self.payload = ipc_codec.encode(self.snapshot)
        self.identity = self.snapshot.fingerprints
        self.check_frame(ipc_codec.decode(self.payload))
        data.schema.validate(self.expected)

    def check_frame(self, result: object) -> None:
        """Check native fidelity including signed zero and spatial metadata."""
        if isinstance(result, Snapshot):
            result = native_frame(result)
        if isinstance(result, Locations):
            result = result.to_geodataframe()
        assert_equal(cast("pd.DataFrame", result), self.expected)

    def check_identity(self, result: object) -> None:
        """Check an exact fingerprint tuple."""
        require(result == self.identity, "Fingerprint changed")

    def fresh(self) -> Snapshot:
        """Return an owned snapshot whose identity has not been computed."""
        return (
            location_snapshot(self.data.build())
            if self.data.value is not None
            else Snapshot.from_frame(self.data.frame, self.data.schema)
        )

    def operations(self) -> list[str]:
        """Return independent boundaries and graph scenarios."""
        names = [
            "construction",
            "validation",
            "conversion",
            "ownership",
            "native_export",
            "selection",
            "hash_properties",
            "hash_first",
            "hash_reuse",
            "encode_first",
            "encode_reuse",
            "decode",
            "publish_first",
            "read",
            "read_validated",
        ]
        if self.data.value is not None:
            names += ["hash_geometry", "publish_reuse"]
        shapes = (
            ("chain", "fanout", "shared")
            if self.data.case.kind == "rich"
            else ("shared",)
        )
        return names + [
            f"graph:{shape}:{mode}"
            for shape in shapes
            for mode in ("off", "cold", "full", "intermediate")
        ]

    def prepare(self, name: str, directory: Path) -> Operation:
        """Return independently prepared mutable state before timing.

        Raises:
            ValueError: If the operation is unknown.
        """
        if name.startswith("graph:"):
            _, shape, mode = name.split(":")
            return self.prepare_graph(shape, mode, directory)
        metadata = {"artifact_bytes": len(self.payload)}
        if name in {
            "construction",
            "validation",
            "conversion",
            "ownership",
            "native_export",
            "selection",
        }:
            return self.prepare_basic(name, metadata)
        if name in {
            "hash_properties",
            "hash_geometry",
            "hash_first",
            "hash_reuse",
            "encode_first",
            "encode_reuse",
            "decode",
        }:
            return self.prepare_identity(name, metadata)
        if name in {"publish_first", "publish_reuse", "read", "read_validated"}:
            return self.prepare_cache(name, directory, metadata)
        message = f"Unknown operation: {name}"
        raise ValueError(message)

    def prepare_basic(self, name: str, metadata: dict) -> Operation:
        """Return a prepared construction, validation, conversion, or selection.

        Raises:
            ValueError: If the operation is unknown.
        """
        if name == "construction":
            return Operation(self.fresh, self.check_frame, metadata)
        if name == "validation":

            def validate() -> object:
                if self.data.output is not None and self.data.value is not None:
                    return self.data.output.validate(self.data.value)
                return self.data.schema.validate(self.data.frame)

            return Operation(validate, self.check_frame, metadata)
        if name == "conversion":
            return Operation(
                lambda: arrow_conversion.to_arrow(self.data.frame, self.data.schema),
                lambda result: require(
                    cast("pa.Table", result).equals(self.snapshot.table),
                    "Arrow conversion changed contents",
                ),
                metadata,
            )
        if name == "ownership":
            return Operation(
                lambda: own(self.snapshot.table),
                lambda result: require(
                    cast("pa.Table", result).equals(self.snapshot.table),
                    "Ownership changed contents",
                ),
                metadata,
            )
        if name == "native_export":
            return Operation(
                lambda: native_frame(self.snapshot), self.check_frame, metadata
            )
        if name == "selection":
            return Operation(
                lambda: self.snapshot.slice(0, min(100, self.data.case.rows)),
                lambda result: assert_equal(
                    native_frame(cast("Snapshot", result)), self.expected.iloc[:100]
                ),
                metadata,
            )
        message = f"Unknown basic operation: {name}"
        raise ValueError(message)

    def prepare_identity(self, name: str, metadata: dict) -> Operation:
        """Return a prepared identity or artifact boundary.

        Raises:
            ValueError: If the operation is unknown.
        """
        if name in {"hash_properties", "hash_geometry"}:
            table = (
                self.snapshot.table
                if name == "hash_properties"
                else self.snapshot.geometry
            )
            require(table is not None, "Geometry required")
            table = cast("pa.Table", table)
            expected = content_fingerprints.typed_table(table)
            return Operation(
                lambda: content_fingerprints.typed_table(table),
                lambda result: require(result == expected, "Component hash changed"),
                metadata,
            )
        if name in {"hash_first", "hash_reuse", "encode_first", "encode_reuse"}:
            snapshot = self.fresh()
            if name.endswith("reuse"):
                self.check_identity(snapshot.fingerprints)
            metadata["identity_cached_before"] = name.endswith("reuse")
            if name.startswith("hash"):
                return Operation(
                    lambda: snapshot.fingerprints, self.check_identity, metadata
                )
            return Operation(
                lambda: ipc_codec.encode(snapshot),
                lambda result: self.check_frame(
                    ipc_codec.decode(cast("bytes", result))
                ),
                metadata,
            )
        if name == "decode":
            return Operation(
                lambda: ipc_codec.decode(self.payload), self.check_frame, metadata
            )
        message = f"Unknown identity operation: {name}"
        raise ValueError(message)

    def prepare_cache(self, name: str, directory: Path, metadata: dict) -> Operation:
        """Return cache operations without warming a first write."""
        cache = TableCache(directory)
        value = self.data.build() if self.data.value is not None else None
        if value is not None:
            if name == "publish_reuse":
                self.check_identity(location_snapshot(value).fingerprints)
            metadata["identity_cached_before"] = name == "publish_reuse"

        def publish() -> None:
            with cache.lock("sample") as locked:
                require(locked, "Publication lock unavailable")
                if value is not None:
                    cache.write_locations("sample", value)
                else:
                    cache.write(
                        "sample", {"rows": self.data.frame}, {"rows": self.data.schema}
                    )

        def read() -> object:
            if value is not None:
                restored = cache.read_locations("sample")
                require(restored is not None, "Location cache miss")
                return restored
            outputs = cache.read("sample")
            require(outputs is not None, "Metric cache miss")
            return cast("dict", outputs)["rows"]

        def verify(result: object) -> None:
            self.check_frame(read() if name.startswith("publish") else result)
            metadata["cache_directory_bytes"] = directory_bytes(directory)

        if name.startswith("publish"):
            return Operation(publish, verify, metadata)
        publish()

        def read_validated() -> object:
            return self.validate_result(read())

        return Operation(read if name == "read" else read_validated, verify, metadata)

    def validate_result(self, restored: object) -> object:
        """Return a restored value after current-schema validation."""
        if self.data.output is not None:
            return self.data.output.validate(cast("Locations", restored))
        return self.data.schema.validate(cast("pd.DataFrame", restored))

    def prepare_graph(self, shape: str, mode: str, directory: Path) -> Operation:
        """Return a seeded graph action with untimed handler checks."""
        runner, calls = graph(self.data, shape, None if mode == "off" else directory)
        if mode in {"full", "intermediate"}:
            runner.run("root", {})
            if mode == "intermediate":
                remove_consumers(directory, spatial=self.data.value is not None)
            calls.clear()
        metadata = {"handlers": calls}

        def verify(result: object) -> None:
            outputs = cast("dict", result)
            assert_equal(
                outputs["root"], pd.DataFrame({"count": [2 * self.data.case.rows]})
            )
            expected = (
                []
                if mode == "full"
                else ["left", "root"]
                if shape == "chain"
                else ["left", "right", "root"]
            )
            if mode in {"full", "intermediate"}:
                require(calls == expected, f"Unexpected cache handlers: {calls}")
            else:
                require(
                    calls.count("source") == 1 and calls.count("root") == 1,
                    f"Unexpected fresh handlers: {calls}",
                )
            metadata["cache_directory_bytes"] = (
                sum(
                    member.stat().st_size
                    for member in directory.rglob("*")
                    if member.is_file()
                )
                if directory.exists()
                else 0
            )

        return Operation(lambda: runner.run("root", {}), verify, metadata)


def remove_consumers(directory: Path, *, spatial: bool) -> None:
    """Retain source/intermediate artifacts and remove final consumer pointers."""
    for pointer in directory.iterdir():
        if not pointer.is_symlink():
            continue
        manifest = json.loads((pointer / "manifest.json").read_text(encoding="utf-8"))
        if manifest["kind"] == "metric" and (
            spatial or not set(manifest["outputs"]) & {"rows", "forwarded"}
        ):
            pointer.unlink()


def directory_bytes(directory: Path) -> int:
    """Return bytes in regular files, excluding symlink-directory duplication."""
    return sum(
        member.stat().st_size for member in directory.rglob("*") if member.is_file()
    )


def native_frame(snapshot: Snapshot) -> pd.DataFrame:
    """Return native properties and geometry for either snapshot role."""
    if snapshot.scope is None or snapshot.scope.metric:
        return snapshot.to_frame()
    frame = snapshot.to_frame()
    frame[snapshot.scope.geometry_column] = geometry_shapely.frame_geometries(snapshot)
    return gpd.GeoDataFrame(
        frame[list(snapshot.scope.columns)],
        geometry=snapshot.scope.geometry_column,
        crs=snapshot.scope.crs,
    )
