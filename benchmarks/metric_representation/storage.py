"""Experimental ownership boundaries for native metric outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Literal, cast

import geopandas as gpd
import pandas as pd
import pyarrow as pa

if TYPE_CHECKING:
    from collections.abc import Hashable, Sequence

    from lyra.sdk.results import NativeOutputs
    from pandas._typing import DtypeObj, SequenceNotStr

Mode = Literal["native", "isolated", "arrow"]
MODES: tuple[Mode, ...] = ("native", "isolated", "arrow")


def owned_index(index: pd.Index) -> pd.Index:
    """Copy index storage, including categorical dictionaries and levels.

    Returns:
        An index with independent writable buffers.
    """
    if isinstance(index, pd.MultiIndex):
        return pd.MultiIndex(
            # pandas accepts Index and NumPy arrays; its stubs narrow both.
            levels=[
                cast("SequenceNotStr[Hashable]", owned_index(level))
                for level in index.levels
            ],
            codes=[cast("Sequence[int]", code.copy()) for code in index.codes],
            names=index.names,
        )
    if isinstance(index, pd.CategoricalIndex):
        return pd.CategoricalIndex(
            pd.Categorical.from_codes(
                index.codes.copy(),
                categories=index.categories.copy(deep=True),
                ordered=cast("pd.CategoricalDtype", index.dtype).ordered,
            ),
            name=index.name,
        )
    return index.copy(deep=True)


def owned_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Detach writable arrays for the scalar-valued tables supported by Lyra.

    Returns:
        A native table with independent mutable arrays.
    """
    result = frame.copy(deep=True)
    result.index = owned_index(frame.index)
    result.columns = owned_index(frame.columns)
    for name in frame.select_dtypes(include="category"):
        column = frame[name].cat
        result[name] = pd.Categorical.from_codes(
            column.codes.to_numpy(copy=True),
            categories=column.categories.copy(deep=True),
            ordered=column.ordered,
        )
    return result


def _owned_dtype(dtype: DtypeObj) -> DtypeObj:
    if isinstance(dtype, pd.CategoricalDtype):
        return pd.CategoricalDtype(
            categories=owned_index(dtype.categories), ordered=dtype.ordered
        )
    return dtype


@dataclass
class ArrowSnapshot:
    """Owned Arrow buffers with pandas dtype and index restoration metadata.

    Uncompressed IPC into immutable bytes is a conservative ownership prototype,
    not a proposed production buffer-copy implementation or disk cache format.
    Spatial tables use GeoArrow WKB; no geometry compression is being evaluated.
    """

    table: pa.Table
    dtypes: pd.Series
    index_dtypes: list[DtypeObj]
    spatial: bool
    index_levels: tuple[pd.Index, ...] | None

    @classmethod
    def freeze(cls, frame: pd.DataFrame) -> ArrowSnapshot:
        """Detach all buffers from producer-owned mutable arrays.

        Returns:
            An owned snapshot independent of the producer.
        """
        spatial = isinstance(frame, gpd.GeoDataFrame)
        table = (
            pa.table(frame.to_arrow(geometry_encoding="WKB"))
            if spatial
            else pa.Table.from_pandas(frame)
        )
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        # Immutable backing also prevents writable NumPy exports from modifying
        # a retained snapshot. Merely wrapping a DataFrame with Arrow does not.
        table = pa.ipc.open_file(sink.getvalue().to_pybytes()).read_all()
        indices = (
            frame.index.levels
            if isinstance(frame.index, pd.MultiIndex)
            else [frame.index]
        )
        return cls(
            table,
            frame.dtypes.map(_owned_dtype),
            [_owned_dtype(i.dtype) for i in indices],
            spatial,
            tuple(owned_index(level) for level in frame.index.levels)
            if isinstance(frame.index, pd.MultiIndex)
            else None,
        )

    def export(self) -> pd.DataFrame:
        """Restore a mutable native table, preserving the declared pandas types.

        Returns:
            An independent consumer view with restored types and index metadata.
        """
        frame = (
            gpd.GeoDataFrame.from_arrow(self.table)
            if self.spatial
            else self.table.to_pandas()
        )
        for name, dtype in self.dtypes.items():
            if frame[name].dtype != dtype:
                frame[name] = frame[name].astype(_owned_dtype(dtype))
            if isinstance(dtype, pd.CategoricalDtype):
                # Arrow dictionary codes can be read-only even when numeric
                # pandas blocks are writable. Consumers must be able to edit them.
                frame[name] = frame[name].copy(deep=True)
        for level, dtype in enumerate(self.index_dtypes):
            if isinstance(frame.index, pd.MultiIndex):
                if frame.index.levels[level].dtype != dtype:
                    frame.index = frame.index.set_levels(
                        cast(
                            "SequenceNotStr[Hashable]",
                            frame.index.levels[level].astype(dtype),
                        ),
                        level=level,
                    )
            elif frame.index.dtype != dtype:
                frame.index = frame.index.astype(_owned_dtype(dtype))
        if self.index_levels is not None:
            frame.index = pd.MultiIndex(
                levels=[
                    cast("SequenceNotStr[Hashable]", owned_index(level))
                    for level in self.index_levels
                ],
                codes=[
                    cast(
                        "Sequence[int]",
                        level.get_indexer(frame.index.get_level_values(number)),
                    )
                    for number, level in enumerate(self.index_levels)
                ],
                names=frame.index.names,
            )
        return frame


@dataclass
class ResultStore:
    """Retain one representation per result and count every consumer export."""

    mode: Mode
    entries: dict[str, dict[str, pd.DataFrame | ArrowSnapshot]] = field(
        default_factory=dict
    )
    stored_tables: int = 0
    exported_tables: int = 0
    freeze_seconds: float = 0.0
    export_seconds: float = 0.0

    def put(self, key: str, outputs: NativeOutputs) -> None:
        """Freeze each newly produced or cache-restored table exactly once."""
        start = perf_counter()
        self.stored_tables += len(outputs)
        if self.mode == "arrow":
            self.entries[key] = {
                name: ArrowSnapshot.freeze(frame) for name, frame in outputs.items()
            }
        elif self.mode == "isolated":
            self.entries[key] = {
                name: owned_frame(frame) for name, frame in outputs.items()
            }
        else:
            self.entries[key] = dict(outputs)
        self.freeze_seconds += perf_counter() - start

    def get(self, key: str) -> NativeOutputs:
        """Export independent consumer tables except in the shared baseline.

        Returns:
            The complete native output bundle.
        """
        start = perf_counter()
        self.exported_tables += len(self.entries[key])
        result = {
            name: value.export()
            if isinstance(value, ArrowSnapshot)
            else owned_frame(value)
            if self.mode == "isolated"
            else value
            for name, value in self.entries[key].items()
        }
        self.export_seconds += perf_counter() - start
        return result
