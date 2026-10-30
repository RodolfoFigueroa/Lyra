"""Atomic, host-local storage of validated tables and internal location results."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import geopandas as gpd
import pandas as pd
from filelock import FileLock
from lyra.sdk import location_artifacts
from lyra.sdk.models.location import (
    Locations,
    location_snapshot,
    locations_from_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterator

    from lyra.sdk.results import NativeOutputs
    from pandas._typing import SequenceNotStr

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
                name: self._read_table(directory, index, metadata)
                for index, (name, metadata) in enumerate(manifest["outputs"].items())
            }
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Unreadable cache entry: %s", key, exc_info=True)
            return None

    def write(self, key: str, outputs: NativeOutputs) -> None:
        """Publish all outputs atomically while the caller holds the key lock."""
        try:
            self._publish(key, outputs)
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
                {"version": 1, "kind": kind, "members": members, "outputs": outputs}
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
            or manifest["version"] != 1
            or manifest["kind"] != kind
            or not isinstance(manifest["outputs"], dict)
        ):
            message = "Unsupported bundle manifest"
            raise ValueError(message)
        expected = (
            {"locations.arrow"}
            if kind == "location_operation"
            else {
                filename
                for index, metadata in enumerate(manifest["outputs"].values())
                for filename in [
                    f"{index}.parquet",
                    *(
                        f"{index}-category-{ordinal}.parquet"
                        for ordinal in range(len(metadata["categories"]))
                    ),
                ]
            }
        )
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

    def _publish(self, key: str, outputs: NativeOutputs) -> None:
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            manifest = {}
            for index, (name, table) in enumerate(outputs.items()):
                table.to_parquet(directory / f"{index}.parquet")
                manifest[name] = self._metadata(directory, index, table)
            self._manifest(directory, "metric", manifest)
            self._replace(key, directory)

    @staticmethod
    def _metadata(directory: Path, number: int, table: pd.DataFrame) -> dict:
        categories: list[tuple[str, str | int, pd.CategoricalDtype]] = [
            ("column", str(name), cast("pd.CategoricalDtype", table[name].dtype))
            for name in table.select_dtypes(include="category")
        ]
        indices = (
            table.index.levels
            if isinstance(table.index, pd.MultiIndex)
            else [table.index]
        )
        for level, index in enumerate(indices):
            if isinstance(index, pd.CategoricalIndex):
                categories.append(
                    ("index", level, cast("pd.CategoricalDtype", index.dtype))
                )
        descriptors = []
        for ordinal, (kind, name, dtype) in enumerate(categories):
            filename = f"{number}-category-{ordinal}.parquet"
            pd.DataFrame({"value": dtype.categories}).to_parquet(directory / filename)
            descriptors.append(
                {
                    "kind": kind,
                    "name": name,
                    "ordered": dtype.ordered,
                    "category_name": dtype.categories.name,
                }
            )
        return {
            "spatial": isinstance(table, gpd.GeoDataFrame),
            "categories": descriptors,
            "dtypes": {
                str(name): TableCache._dtype(dtype)
                for name, dtype in table.dtypes.items()
            },
            "index_dtypes": [TableCache._dtype(index.dtype) for index in indices],
        }

    @staticmethod
    def _read_table(directory: Path, number: int, metadata: dict) -> pd.DataFrame:
        reader = gpd.read_parquet if metadata["spatial"] else pd.read_parquet
        table = reader(directory / f"{number}.parquet")
        for name, dtype in metadata["dtypes"].items():
            if dtype is not None:
                table[name] = table[name].astype(TableCache._restore_dtype(dtype))
        for level, dtype in enumerate(metadata["index_dtypes"]):
            if dtype is None:
                continue
            restored = TableCache._restore_dtype(dtype)
            if isinstance(table.index, pd.MultiIndex):
                # pandas accepts Index; its stubs require a narrower sequence protocol.
                table.index = table.index.set_levels(
                    cast(
                        "SequenceNotStr[Hashable]",
                        table.index.levels[level].astype(restored),
                    ),
                    level=level,
                )
            else:
                table.index = table.index.astype(restored)
        for ordinal, descriptor in enumerate(metadata["categories"]):
            values = pd.read_parquet(
                directory / f"{number}-category-{ordinal}.parquet"
            )["value"]
            dtype = pd.CategoricalDtype(
                pd.Index(values.array, name=descriptor["category_name"]),
                ordered=descriptor["ordered"],
            )
            if descriptor["kind"] == "column":
                name = descriptor["name"]
                table[name] = table[name].astype(dtype)
            elif isinstance(table.index, pd.MultiIndex):
                level = descriptor["name"]
                table.index = table.index.set_levels(
                    table.index.levels[level].astype(dtype), level=level
                )
            else:
                table.index = table.index.astype(dtype)
        return table

    @staticmethod
    def _dtype(dtype: object) -> dict | None:
        if isinstance(dtype, pd.CategoricalDtype):
            return None
        if isinstance(dtype, pd.StringDtype):
            return {
                "name": "string",
                "storage": dtype.storage,
                "nullable": dtype.na_value is pd.NA,
            }
        return {"name": str(dtype)}

    @staticmethod
    def _restore_dtype(descriptor: dict) -> str | pd.StringDtype:
        if "storage" in descriptor:
            return pd.StringDtype(
                storage=descriptor["storage"],
                na_value=pd.NA if descriptor["nullable"] else float("nan"),
            )
        return descriptor["name"]
