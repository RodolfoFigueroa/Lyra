"""Validate shared location structures without acquiring resources."""

import json
from collections.abc import Sequence
from typing import cast

from lyra.contracts.plugins import NodeManifest


def _structure(value: object) -> object:
    if isinstance(value, list):
        return [_structure(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: item if key in {"parameters", "options"} else _structure(item)
        for key, item in value.items()
        if key not in {"description", "meaning", "granularity"}
    }
    for key in ("checks", "columns"):
        if key in result and (key != "columns" or not result.get("ordered")):
            result[key] = sorted(
                cast("list[object]", result[key]),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
    return result


def validate_location_families(nodes: Sequence[NodeManifest]) -> None:
    """Check that location contracts sharing a family agree structurally.

    Raises:
        ValueError: If a location family has conflicting structures.
    """
    location_families: dict[tuple[str, str], tuple[str, object]] = {}
    for node in nodes:
        for binding in [
            *(value.contract for value in node.location_inputs.values()),
            *([node.output_contract] if node.kind == "location_operation" else []),
        ]:
            family = binding.family
            key = (family.collection, family.unit_type)
            structure = _structure(family.structure.model_dump(mode="json"))
            if key in location_families and location_families[key][1] != structure:
                message = (
                    f"Location family {key!r}: variants {location_families[key][0]!r} "
                    f"and {node.name!r} have incompatible structure"
                )
                raise ValueError(message)
            location_families[key] = (node.name, structure)
