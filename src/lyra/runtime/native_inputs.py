"""Private native records and explicit ownership boundaries for execution."""

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import TypeVar

import geopandas as gpd
import pandas as pd

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.outputs import OutputMetadata

Frame = TypeVar("Frame", bound=pd.DataFrame)


def copy_frame(frame: Frame) -> Frame:
    """Return detached native buffers and nested metadata without copy-on-write."""
    result = frame.copy(deep=True)
    result.attrs = deepcopy(frame.attrs)
    return result


@dataclass(frozen=True)
class NativeLocationInput:
    """Execution-owned records with scope and original identities kept separately."""

    frame: gpd.GeoDataFrame
    contract: LocationContractReference
    id_column: str = "location_id"
    ids: tuple[str, ...] = field(init=False)
    source_scope: LocationContractReference | None = field(default=None, kw_only=True)
    description: OutputMetadata | None = field(default=None, kw_only=True)

    lineage: object = field(default_factory=object, repr=False, compare=False)
    source: "NativeLocationInput | None" = field(
        default=None, repr=False, compare=False
    )

    def related_selection(
        self, primary: "NativeLocationInput"
    ) -> "NativeLocationInput | None":
        """Select via explicit lineage, retaining immediate correspondence pairs.

        Walk each hop separately: target ownership maps back to source groups,
        and source demand maps forward to pairs. No transitive rows are expanded.

        Returns:
            Related records, or None for independently bound selections.
        """
        ancestors = {}
        current = primary
        ids = frozenset(current.ids)
        while True:
            ancestors[current.lineage] = ids
            if current.source is None:
                break
            ids = frozenset(
                current.frame.loc[
                    current.frame[current.id_column].isin(ids), "source_location_id"
                ]
            )
            current = current.source
        chain = []
        current = self
        while current.lineage not in ancestors:
            if current.source is None:
                return None
            chain.append(current)
            current = current.source
        ids = ancestors[current.lineage]
        selected = replace(
            current, frame=current.frame.loc[current.frame[current.id_column].isin(ids)]
        )
        for hop in reversed(chain):
            selected = replace(
                hop,
                frame=hop.frame.loc[hop.frame["source_location_id"].isin(selected.ids)],
            )
        return selected

    def __post_init__(self) -> None:
        """Capture records and identity before any handler receives working data."""
        object.__setattr__(self, "frame", copy_frame(self.frame))
        object.__setattr__(self, "contract", self.contract.model_copy(deep=True))
        object.__setattr__(
            self, "ids", tuple(dict.fromkeys(self.frame[self.id_column]))
        )

        for name in ("source_scope", "description"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.model_copy(deep=True))


def input_fingerprint(value: NativeLocationInput, *, canonical: bool = False) -> str:
    """Return a hash of captured scope and contents, excluding frame metadata."""
    frame = value.frame
    if canonical:
        frame = frame.sort_values(
            ["source_location_id", value.id_column]
            if value.source_scope is not None
            else [value.id_column]
        )
    frame = frame.reset_index(drop=True)
    digest = sha256(
        json.dumps(value.contract.identity_payload, sort_keys=True).encode()
    )
    if value.source_scope is not None:
        digest.update(
            json.dumps(value.source_scope.identity_payload, sort_keys=True).encode()
        )
    digest.update(str(frame.crs).encode())
    for name in sorted(frame.columns):
        series = frame[name]
        digest.update(str((name, str(series.dtype))).encode())
        values = series.to_wkb() if isinstance(series, gpd.GeoSeries) else series
        digest.update(
            pd.util.hash_pandas_object(values, index=False).to_numpy().tobytes()
        )
    return digest.hexdigest()
