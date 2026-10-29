"""Atomic, host-local storage of validated metric tables."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

import geopandas as gpd
import pandas as pd
from filelock import FileLock

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
        lock = FileLock(
            self.directory / f"{key}.lock",
            fallback_to_soft=False,
            preserve_lock_file=True,
        )
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            lock.acquire()
        except OSError:
            logger.warning("Cache lock unavailable: %s", key, exc_info=True)
            yield False
            return
        try:
            yield True
        finally:
            try:
                lock.release()
            except OSError:
                logger.warning("Cache unlock failed: %s", key, exc_info=True)

    def read(self, key: str) -> NativeOutputs | None:
        """Read a complete bundle.

        Returns:
            Tables, or None for an unreadable or absent entry.
        """
        directory = self.directory / key
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            return {
                name: self._read_table(directory, index, metadata)
                for index, (name, metadata) in enumerate(manifest.items())
            }
        except FileNotFoundError:
            return None
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            NotImplementedError,
        ):
            logger.warning("Unreadable cache entry: %s", key, exc_info=True)
            return None

    def write(self, key: str, outputs: NativeOutputs) -> None:
        """Publish all outputs atomically while the caller holds the key lock."""
        try:
            self._publish(key, outputs)
        except OSError:
            logger.warning("Cache publication failed: %s", key, exc_info=True)

    def _publish(self, key: str, outputs: NativeOutputs) -> None:
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            manifest = {}
            for index, (name, table) in enumerate(outputs.items()):
                table.to_parquet(directory / f"{index}.parquet")
                manifest[name] = self._metadata(directory, index, table)
            (directory / "manifest.json").write_text(json.dumps(manifest))
            destination = self.directory / key
            if destination.exists():
                shutil.rmtree(destination)
            directory.rename(destination)

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
