"""Select immediate correspondence rows and completed computations by lineage."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Selection:
    """Selected owners and computation references, with positions in the last hop."""

    ids: tuple[str, ...] | None
    positions: tuple[int, ...] | None
    computations: tuple[tuple[str, str | None], ...]

    @property
    def acquired_ids(self) -> tuple[str, ...]:
        """Known owners at a frame or retention boundary.

        Raises:
            ValueError: If operation membership is still unresolved.
        """
        if self.ids is None:
            msg = "Location membership must be resolved before materialization"
            raise ValueError(msg)
        return self.ids


class PairIndex:
    """Ordered row positions for a single immutable correspondence snapshot."""

    def __init__(self, pairs: tuple[tuple[str, str], ...]) -> None:
        """Index both endpoints once for forward and reverse traversal."""
        self.by_source: dict[str, list[int]] = {}
        self.by_target: dict[str, list[int]] = {}
        for position, (source, target) in enumerate(pairs):
            self.by_source.setdefault(source, []).append(position)
            self.by_target.setdefault(target, []).append(position)

    def positions(self, ids: frozenset[str], *, targets: bool) -> tuple[int, ...]:
        """Return matching row positions in snapshot order."""
        buckets = self.by_target if targets else self.by_source
        return tuple(sorted(pos for key in ids for pos in buckets.get(key, ())))


@dataclass(frozen=True, eq=False)
class Membership:
    """Frame-independent membership of one binding, before or after acquisition."""

    lineage: object
    ids: tuple[str, ...] | None
    pairs: tuple[tuple[str, str], ...]
    computations: tuple[tuple[str, str | None], ...]
    source: Membership | None

    @cached_property
    def owner_ids(self) -> frozenset[str] | None:
        """Known target membership for this snapshot."""
        return frozenset(self.ids) if self.ids is not None else None

    @cached_property
    def pair_index(self) -> PairIndex:
        """Position buckets owned by this membership snapshot."""
        return PairIndex(self.pairs)

    def _computations(
        self, sources: frozenset[str]
    ) -> tuple[tuple[str, str | None], ...]:
        return tuple(
            item for item in self.computations if item[1] is None or item[1] in sources
        )

    def targets(self, ids: frozenset[str] | None) -> Selection:
        """Return incoming rows for the requested owners in snapshot order."""
        if self.ids is None or ids is None:
            return Selection(None, None, self.computations)
        complete = ids == self.owner_ids
        if complete:
            computations = self.computations
            if self.pairs and computations:
                computations = self._computations(frozenset(self.pair_index.by_source))
            return Selection(self.ids, None, computations)
        if self.source is None and not self.pairs:
            return Selection(
                tuple(sorted(ids.intersection(self.owner_ids or ()))),
                None,
                self._computations(ids),
            )
        positions = self.pair_index.positions(ids, targets=True)
        sources = frozenset(self.pairs[pos][0] for pos in positions)
        return Selection(
            tuple(sorted({self.pairs[pos][1] for pos in positions})),
            positions,
            self._computations(sources),
        )

    def from_sources(self, ids: tuple[str, ...] | None) -> Selection:
        """Return outgoing rows and completed groups for selected sources."""
        if ids is None or self.ids is None:
            return Selection(None, None, self.computations)
        sources = frozenset(ids)
        positions = self.pair_index.positions(sources, targets=False)
        return Selection(
            tuple(sorted({self.pairs[pos][1] for pos in positions})),
            positions,
            self._computations(sources),
        )

    def related(
        self, primary: Membership, *, owners: frozenset[str] | None = None
    ) -> Selection | None:
        """Return related membership via the nearest common ancestor, if one exists."""
        if self is primary:
            if owners is not None:
                return self.targets(owners)
            return Selection(self.ids, None, self.computations)
        ancestors = {}
        current = primary
        ids = primary.owner_ids
        if ids is not None and owners is not None:
            ids = ids.intersection(owners)
        while True:
            ancestors[current.lineage] = ids
            if current.source is None:
                break
            if ids is not None:
                ids = frozenset(
                    current.pairs[pos][0]
                    for pos in current.pair_index.positions(ids, targets=True)
                )
            current = current.source
        chain = []
        current = self
        while current.lineage not in ancestors:
            if current.source is None:
                return None
            chain.append(current)
            current = current.source
        selected = current.targets(ancestors[current.lineage])
        for hop in reversed(chain):
            selected = hop.from_sources(selected.ids)
        return selected
