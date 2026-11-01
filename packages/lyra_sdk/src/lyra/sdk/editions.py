"""Explicit metric family registration and concrete edition selection."""

from collections.abc import Sequence
from types import MappingProxyType

from lyra.sdk.models.plugin import NodeManifest


class EditionRegistry:
    """Index concrete metric names independently of dependency edges.

    A missing edition represents the sole implicit variant. Named editions always
    require a selector, even when only one variant is installed.
    """

    def __init__(self, nodes: Sequence[NodeManifest]) -> None:
        """Collect families without acquiring resources.

        Raises:
            ValueError: If registrations duplicate or mix implicit and named editions.
        """
        families: dict[str, dict[str | None, str]] = {}
        for node in nodes:
            if node.kind != "metric":
                continue
            family = node.family or node.name
            variants = families.setdefault(family, {})
            if node.edition in variants:
                message = f"Duplicate edition {node.edition!r} in family {family!r}"
                raise ValueError(message)
            if variants and (node.edition is None or None in variants):
                message = f"Family {family!r} mixes implicit and named editions"
                raise ValueError(message)
            variants[node.edition] = node.name
        self.families = MappingProxyType(
            {name: MappingProxyType(variants) for name, variants in families.items()}
        )

    def resolve(self, family: str, *, edition: str | None = None) -> str:
        """Return a concrete node name for an explicit family selection.

        Raises:
            ValueError: If the family or edition is unknown or selection is omitted.
        """
        variants = self.families.get(family)
        if variants is None:
            message = f"Unknown metric family {family!r}"
            raise ValueError(message)
        if edition is None and None not in variants:
            message = f"Family {family!r} requires explicit edition selection"
            raise ValueError(message)
        if edition not in variants:
            message = f"Unknown edition {edition!r} for family {family!r}"
            raise ValueError(message)
        return variants[edition]
