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


def validate_provider_structures(nodes: Sequence[NodeManifest]) -> None:
    """Check that declarations sharing a provider ID agree structurally.

    Raises:
        ValueError: If a provider has conflicting structures.
    """
    structures: dict[str, tuple[str, object]] = {}
    for node in nodes:
        for binding in [
            *(value.contract for value in node.location_inputs.values()),
            *([node.output_contract] if node.kind == "location_operation" else []),
        ]:
            key = binding.provider_scope.provider_id
            structure = _structure(binding.structure.model_dump(mode="json"))
            if key in structures and structures[key][1] != structure:
                message = (
                    f"Provider {key!r}: variants {structures[key][0]!r} "
                    f"and {node.name!r} have incompatible structure"
                )
                raise ValueError(message)
            structures[key] = (node.name, structure)
