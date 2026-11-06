"""Explicit metric family registration and concrete edition selection."""

import json
from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import cast

from lyra.contracts.plugins import MetricManifest, NodeManifest
from lyra.contracts.schema import resolve_schema_reference


def _sort_schema_lists(value: dict[str, object]) -> None:
    for key in ("required", "enum", "type", "allOf", "anyOf", "oneOf"):
        items = value.get(key)
        if isinstance(items, list):
            value[key] = sorted(
                items, key=lambda item: json.dumps(item, sort_keys=True)
            )


def _normalize_lists(value: dict, stack: tuple[str, ...], normalize: Callable) -> None:
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if key in value:
            value[key] = [normalize(child, stack) for child in value[key]]


def _parameter_contract(node: NodeManifest) -> str:
    root = node.parameters_schema or {}

    def normalize(value: object, stack: tuple[str, ...] = ()) -> object:
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key in ("$defs", "title", "description", "examples", "$schema"):
            value.pop(key, None)
        reference = value.pop("$ref", None)
        if reference is not None:
            if reference in stack:
                value["$ref"] = f"recursive:{len(stack) - stack.index(reference)}"
            else:
                target = normalize(
                    resolve_schema_reference(root, reference), (*stack, reference)
                )
                return (
                    normalize({"allOf": [target], **value}, stack) if value else target
                )
        for key in ("properties", "patternProperties", "dependentSchemas"):
            if isinstance(value.get(key), dict):
                value[key] = {
                    name: normalize(child, stack) for name, child in value[key].items()
                }
        for key in (
            "items",
            "contains",
            "additionalProperties",
            "unevaluatedProperties",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
            "unevaluatedItems",
        ):
            if key in value:
                value[key] = normalize(value[key], stack)
        _normalize_lists(value, stack, normalize)
        _sort_schema_lists(value)
        return value

    return json.dumps(normalize(root), sort_keys=True)


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


def _contract(node: MetricManifest) -> dict[str, object]:
    return {
        "native inputs": {
            name: _structure(value.contract.family.model_dump(mode="json"))
            for name, value in node.location_inputs.items()
        },
        "outputs": {
            name: _structure(output.model_dump(mode="json"))
            for name, output in node.outputs.items()
        },
        "ordinary parameters": _parameter_contract(node),
    }


def _validate_location_families(nodes: Sequence[NodeManifest]) -> None:
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


class EditionRegistry:
    """Index concrete metric names independently of dependency edges.

    A missing edition represents the sole implicit variant. Named editions always
    require a selector, even when only one variant is installed.
    ``contracts`` retains canonical JSON for each authoritative shared contract;
    concrete descriptions and bindings remain on the original node manifests.
    """

    def __init__(self, nodes: Sequence[NodeManifest]) -> None:
        """Collect families without acquiring resources.

        Raises:
            ValueError: If registrations conflict or family contracts differ.
        """
        contracts: dict[str, dict[str, object]] = {}
        representatives: dict[str, str] = {}
        _validate_location_families(nodes)
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
            contract = _contract(node)
            if family in contracts:
                for area, value in contract.items():
                    if contracts[family][area] != value:
                        message = (
                            f"Metric family {family!r}: variants "
                            f"{representatives[family]!r} "
                            f"and {node.name!r} have incompatible {area}"
                        )
                        raise ValueError(message)
            else:
                contracts[family] = contract
                representatives[family] = node.name
            variants[node.edition] = node.name
        self.contracts = MappingProxyType(
            {
                name: json.dumps(contract, sort_keys=True)
                for name, contract in contracts.items()
            }
        )
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
