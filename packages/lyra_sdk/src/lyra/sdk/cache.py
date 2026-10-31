"""Atomic, host-local storage of validated tables and internal location results."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from filelock import FileLock
from lyra.sdk import ipc_codec, location_artifacts
from lyra.sdk.models.location import (
    Locations,
    location_snapshot,
    locations_from_snapshot,
)
from lyra.sdk.typed_storage import Snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import pandera.pandas as pa
    from lyra.sdk.results import NativeOutputs

logger = logging.getLogger(__name__)


class TableCache:
    """Store complete invocation bundles in one local filesystem directory."""

    def __init__(self, directory: Path) -> None:
        """Resolve the caller-owned cache namespace."""
        self.directory = directory.resolve()

    @staticmethod
    def key(identity: str) -> str:
        """Return a filesystem-safe digest of effective invocation inputs."""
        return hashlib.sha256(identity.encode()).hexdigest()

    @contextmanager
    def lock(self, key: str) -> Iterator[bool]:
        """Hold an operating-system lock.

        Yields:
            Whether storage locking succeeded.
        """
        try:
            lock = FileLock(
                self.directory / f"{key}.lock",
                fallback_to_soft=False,
                preserve_lock_file=True,
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            lock.acquire()
        except Exception:
            logger.warning("Cache lock unavailable: %s", key, exc_info=True)
            yield False
            return
        try:
            yield True
        finally:
            try:
                lock.release()
            except Exception:
                logger.warning("Cache unlock failed: %s", key, exc_info=True)

    def read(self, key: str) -> NativeOutputs | None:
        """Read a complete bundle.

        Returns:
            Tables, or None for an unreadable or absent entry.
        """
        try:
            directory, manifest = self._bundle(key, "metric")
            return {
                name: ipc_codec.decode(
                    (directory / f"{index}.arrow").read_bytes()
                ).to_frame()
                for index, name in enumerate(manifest["outputs"])
            }
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Unreadable cache entry: %s", key, exc_info=True)
            return None

    def write(
        self,
        key: str,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> None:
        """Publish all outputs atomically while the caller holds the key lock."""
        try:
            self._publish(key, outputs, schemas)
        except Exception:
            logger.warning("Cache publication failed: %s", key, exc_info=True)

    def read_locations(self, key: str) -> Locations | None:
        """Decode a complete internal location artifact, or return a cache miss.

        Shared invariants are checked here; the executor applies the current
        operation schema before accepting the value.

        Returns:
            Validated locations, or None for an absent or unusable entry.
        """
        try:
            directory, _ = self._bundle(key, "location_operation")
            snapshot = location_artifacts.decode(
                (directory / "locations.arrow").read_bytes()
            )
            return locations_from_snapshot(snapshot)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Unreadable location cache entry: %s", key, exc_info=True)
            return None

    def write_locations(self, key: str, locations: Locations) -> None:
        """Publish normalized locations while the caller holds the invocation lock."""
        try:
            self._publish_locations(key, locations)
        except Exception:
            logger.warning("Location cache publication failed: %s", key, exc_info=True)

    def _publish_locations(self, key: str, locations: Locations) -> None:
        snapshot = location_snapshot(locations)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            (directory / "locations.arrow").write_bytes(
                location_artifacts.encode(snapshot)
            )
            self._manifest(directory, "location_operation", {})
            self._replace(key, directory)

    def _replace(self, key: str, directory: Path) -> None:
        generation = self.directory / f".bundle-{uuid4().hex}"
        pointer = directory.parent / "pointer"
        directory.rename(generation)
        try:
            pointer.symlink_to(generation.name, target_is_directory=True)
            pointer.replace(self.directory / key)
        except BaseException:
            shutil.rmtree(generation)
            raise

    @staticmethod
    def _manifest(directory: Path, kind: str, outputs: dict) -> None:
        members = sorted(path.name for path in directory.iterdir())
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 2 if kind == "metric" else 1,
                    "kind": kind,
                    "members": members,
                    "outputs": outputs,
                }
            )
        )

    def _bundle(self, key: str, kind: str) -> tuple[Path, dict]:
        # Resolve once: a concurrent pointer swap cannot mix generations.
        directory = (self.directory / key).resolve(strict=True)
        if directory.parent != self.directory:
            message = "Bundle outside cache directory"
            raise ValueError(message)
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            message = "Invalid bundle manifest reference"
            raise ValueError(message)
        manifest = json.loads(manifest_path.read_text())
        if (
            set(manifest) != {"version", "kind", "members", "outputs"}
            or type(manifest["version"]) is not int
            or manifest["version"] != (2 if kind == "metric" else 1)
            or manifest["kind"] != kind
            or not isinstance(manifest["outputs"], dict)
        ):
            message = "Unsupported bundle manifest"
            raise ValueError(message)
        expected = (
            {"locations.arrow"}
            if kind == "location_operation"
            else {f"{index}.arrow" for index in range(len(manifest["outputs"]))}
        )
        if kind == "metric" and (
            not manifest["outputs"]
            or any(
                not name or metadata != {}
                for name, metadata in manifest["outputs"].items()
            )
        ):
            message = "Invalid metric outputs"
            raise ValueError(message)

        invalid_location = kind == "location_operation" and bool(manifest["outputs"])
        if invalid_location:
            message = "Unexpected location outputs"
            raise ValueError(message)
        if (
            not isinstance(manifest["members"], list)
            or len(manifest["members"]) != len(expected)
            or set(manifest["members"]) != expected
            or {p.name for p in directory.iterdir()} != expected | {"manifest.json"}
            or any(
                (directory / name).is_symlink() or not (directory / name).is_file()
                for name in expected | {"manifest.json"}
            )
        ):
            message = "Invalid bundle members"
            raise ValueError(message)
        return directory, manifest

    def _publish(
        self,
        key: str,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> None:
        if outputs.keys() != schemas.keys():
            message = "Output names differ from registered schemas"
            raise ValueError(message)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            manifest = {}
            for index, (name, table) in enumerate(outputs.items()):
                snapshot = Snapshot.from_frame(table, schemas[name])
                (directory / f"{index}.arrow").write_bytes(ipc_codec.encode(snapshot))
                manifest[name] = {}
            self._manifest(directory, "metric", manifest)
            self._replace(key, directory)
