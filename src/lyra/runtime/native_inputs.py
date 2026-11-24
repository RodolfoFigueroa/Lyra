"""Private native records and explicit ownership boundaries for execution."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TypeVar

import geopandas as gpd
import pandas as pd

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import EffectiveScope

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

    added_ids: frozenset[str] = field(default_factory=frozenset, kw_only=True)
    computations: tuple[tuple[str, str | None], ...] = field(default=(), kw_only=True)

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
        selected = current.select_ids(ids)
        for hop in reversed(chain):
            selected = replace(
                hop,
                frame=hop.frame.loc[hop.frame["source_location_id"].isin(selected.ids)],
                computations=tuple(
                    (key, group)
                    for key, group in hop.computations
                    if group is None or group in selected.ids
                ),
            )
        return selected

    def select_ids(self, ids: frozenset[str]) -> "NativeLocationInput":
        """Return selected owners with their applicable correspondence references."""
        complete = ids == frozenset(self.ids)
        if complete and (not ids or not self.source_scope or not self.computations):
            return self
        frame = (
            self.frame
            if complete
            else self.frame.loc[self.frame[self.id_column].isin(ids)]
        )
        groups = set(frame["source_location_id"]) if self.source_scope else set(ids)
        computations = tuple(
            (key, group)
            for key, group in self.computations
            if group is None or group in groups
        )
        if complete and computations == self.computations:
            return self
        return replace(
            self,
            frame=frame,
            computations=computations,
        )

    def __post_init__(self) -> None:
        """Capture records and identity before any handler receives working data.

        Raises:
            ValueError: If added records lack an intervention scope.
        """
        object.__setattr__(self, "frame", copy_frame(self.frame))
        object.__setattr__(self, "contract", self.contract.model_copy(deep=True))
        object.__setattr__(
            self, "ids", tuple(dict.fromkeys(self.frame[self.id_column]))
        )

        if self.added_ids and not isinstance(
            self.contract.provider_scope, EffectiveScope
        ):
            msg = "Added records require an effective provider scope"
            raise ValueError(msg)
        object.__setattr__(
            self, "added_ids", frozenset(self.added_ids).intersection(self.ids)
        )

        for name in ("source_scope", "description"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.model_copy(deep=True))

    @property
    def identity_payload(self) -> dict:
        """Qualified records and selected correspondence computation groups."""
        return {
            "provider": self.contract.provider_scope.identity_payload,
            "ids": sorted(set(self.ids)),
            "upstream": [
                {"computation": key, "group": group}
                for key, group in sorted(
                    self.computations, key=lambda item: (item[0], item[1] or "")
                )
            ],
        }


def inputs_equal(left: NativeLocationInput, right: NativeLocationInput) -> bool:
    """Compare scoped native contents after aligning IDs and column names.

    Returns:
        Whether scopes, types, CRS and aligned values are equal.
    """
    if (
        left.contract.identity_payload != right.contract.identity_payload
        or (left.source_scope.identity_payload if left.source_scope else None)
        != (right.source_scope.identity_payload if right.source_scope else None)
        or left.id_column != right.id_column
        or left.frame.crs != right.frame.crs
        or left.frame.active_geometry_name != right.frame.active_geometry_name
    ):
        return False
    if set(left.frame.columns) != set(right.frame.columns):
        return False
    keys = (["source_location_id"] if left.source_scope is not None else []) + [
        left.id_column
    ]
    columns = sorted(left.frame.columns)
    return (
        left.frame.sort_values(keys)[columns]
        .reset_index(drop=True)
        .equals(right.frame.sort_values(keys)[columns].reset_index(drop=True))
    )
