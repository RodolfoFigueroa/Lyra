"""Private native records and explicit ownership boundaries for execution."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import TypeVar

import geopandas as gpd
import pandas as pd

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import EffectiveScope
from lyra.runtime.lineage import Membership, Selection

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
    source: Membership | None = field(default=None, repr=False, compare=False)

    @cached_property
    def membership(self) -> Membership:
        """Row-position lineage for the execution-owned frame."""
        pairs = (
            tuple(
                zip(
                    self.frame["source_location_id"],
                    self.frame[self.id_column],
                    strict=True,
                )
            )
            if self.source_scope
            else ()
        )
        return Membership(self.lineage, self.ids, pairs, self.computations, self.source)

    def _apply(self, selection: Selection) -> "NativeLocationInput":
        if selection.positions is not None:
            frame = self.frame.iloc[list(selection.positions)]
        elif selection.ids == self.ids:
            if selection.computations == self.computations:
                return self
            frame = self.frame
        else:
            frame = self.frame.loc[
                self.frame[self.id_column].isin(selection.acquired_ids)
            ]
        return replace(self, frame=frame, computations=selection.computations)

    def related_selection(
        self, primary: "NativeLocationInput"
    ) -> "NativeLocationInput | None":
        """Return selected records and immediate pairs for a related binding."""
        selection = self.membership.related(primary.membership)
        return self._apply(selection) if selection is not None else None

    def select_ids(self, ids: frozenset[str]) -> "NativeLocationInput":
        """Return selected owners with their applicable correspondence references."""
        return self._apply(self.membership.targets(ids))

    def for_membership(self, selected: Membership) -> "NativeLocationInput":
        """Return the supplied records for a bound selection, in pair order."""
        positions = None
        if selected.pairs != self.membership.pairs:
            by_pair = {pair: index for index, pair in enumerate(self.membership.pairs)}
            positions = tuple(by_pair[pair] for pair in selected.pairs)
        return self._apply(Selection(selected.ids, positions, selected.computations))

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
