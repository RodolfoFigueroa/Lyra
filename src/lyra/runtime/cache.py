"""Atomic, host-local storage of validated tables and internal location results."""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Generic, TypeVar

try:
    from filelock import FileLock
except ModuleNotFoundError as exc:
    if exc.name != "filelock":
        raise
    message = "Install 'lyra[runtime]' to use runtime.cache."
    raise ModuleNotFoundError(message, name=exc.name) from exc

from lyra.runtime.artifacts import ArtifactStore

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping

    import pandera.pandas as pa

    from lyra.data.locations import Locations
    from lyra.data.results import NativeOutputs

logger = logging.getLogger(__name__)


_Value = TypeVar("_Value")


@dataclass(frozen=True)
class CachedArtifact(Generic[_Value]):
    """A validated value and the immutable generation that represents it."""

    value: _Value
    reference: str | None


class TableCache:
    """Store complete invocation bundles in one local filesystem directory."""

    def __init__(self, directory: Path) -> None:
        """Resolve the caller-owned cache namespace."""
        self.store = ArtifactStore(directory)
        self.directory = directory.resolve() / "cache"

    @staticmethod
    def key(identity: str) -> str:
        """Return a filesystem-safe digest of effective invocation inputs."""
        return hashlib.sha256(identity.encode()).hexdigest()

    @contextmanager
    def lock(self, key: str) -> Generator[bool, None, None]:
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

    def reference(self, key: str) -> str | None:
        """Return the published artifact for an invocation, if retained."""
        try:
            return (self.directory / key).read_text()
        except FileNotFoundError:
            return None

    def _publish_reference(self, key: str, reference: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            pointer = Path(scratch) / "reference"
            pointer.write_text(reference)
            pointer.replace(self.directory / key)

    def read(
        self, key: str, validate: Callable[[NativeOutputs], NativeOutputs] | None = None
    ) -> CachedArtifact[NativeOutputs] | None:
        """Return cached tables, or None when storage is unavailable."""
        try:
            reference = self.reference(key)
            if reference is None:
                return None
            stored = self.store.read(reference)
            value = validate(stored) if validate is not None else stored
            unchanged = value.keys() == stored.keys() and all(
                value[name].equals(table)
                and getattr(value[name], "crs", None) == getattr(table, "crs", None)
                for name, table in stored.items()
            )
            return CachedArtifact(value, reference if unchanged else None)
        except Exception:
            logger.warning("Unreadable cache entry: %s", key, exc_info=True)
            return None

    def write(
        self,
        key: str,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> str | None:
        """Return a published artifact ID, or None when publication fails."""
        reference = None
        try:
            reference = self.store.write(outputs, schemas)
            self._publish_reference(key, reference)
        except Exception:
            logger.warning("Cache publication failed: %s", key, exc_info=True)
        return reference

    def read_locations(
        self, key: str, validate: Callable[[Locations], Locations] | None = None
    ) -> CachedArtifact[Locations] | None:
        """Return cached locations, or None when storage is unavailable."""
        try:
            reference = self.reference(key)
            if reference is None:
                return None
            stored = self.store.read_locations(reference)
            value = validate(stored) if validate is not None else stored
            return CachedArtifact(value, reference if value == stored else None)
        except Exception:
            logger.warning("Unreadable location cache entry: %s", key, exc_info=True)
            return None

    def write_locations(self, key: str, locations: Locations) -> str | None:
        """Return a published location artifact ID, or None on failure."""
        reference = None
        try:
            reference = self.store.write_locations(locations)
            self._publish_reference(key, reference)
        except Exception:
            logger.warning("Location cache publication failed: %s", key, exc_info=True)
        return reference
