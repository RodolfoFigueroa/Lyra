"""Prepare effective values for declaration uses without resolving locations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.runtime.planning.graph import ResolvedDependency, ResolvedLocationDependency
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lyra.contracts.accepted import PreparedInput
    from lyra.contracts.types import JsonObject, JsonValue
    from lyra.runtime.planning.catalog import CompiledCatalog


class ParameterUseError(ValueError):
    """Parameter validation failure with its selected receiving path."""

    def __init__(
        self,
        cause: MetricInputError,
        path: tuple[str, ...],
        binding: str | None,
        kind: str,
    ) -> None:
        """Retain context for execution and submission diagnostics."""
        self.cause = cause
        self.path = path
        self.binding = binding
        self.kind = kind
        super().__init__(f"{cause}; path {' -> '.join(path)}, binding {binding!r}")


def use_key(owner: str, values: JsonObject, binding: str) -> str:
    """Return a stable receiving-use key within its declaring caller scope."""
    return json.dumps([owner, values, binding], sort_keys=True, allow_nan=False)


def route_key(name: str, index: int) -> str:
    """Return the key for a selected automatic route hop."""
    return json.dumps(["route", name, index])


def forwarded_parameters(
    source: JsonObject,
    edge: ResolvedDependency | ResolvedLocationDependency,
) -> JsonObject:
    """Forward whole matching values, then apply explicit overrides.

    Returns:
        Values to validate through the receiving model.

    Raises:
        MetricInputError: If a referenced caller value is missing.
    """
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    fields = cast("dict", (producer.parameters_schema or {}).get("properties", {}))
    values = {name: source[name] for name in fields if name in source}
    for name, binding in edge.dependency.parameters.items():
        if isinstance(binding, Input):
            field = binding.path.split(".")[1]
            if field not in source:
                raise MetricInputError(
                    producer.name, binding.path, "missing referenced input"
                )
            values[name] = source[field]
        else:
            values[name] = cast("JsonValue", binding)
    return values


def prepare_parameter_uses(
    catalog: CompiledCatalog,
    node: str,
    values: JsonObject,
    inputs: Sequence[PreparedInput] = (),
    *,
    normalized_uses: Mapping[str, JsonObject] | None = None,
) -> dict[str, JsonObject]:
    """Normalize every selected use through live models, retaining complete JSON.

    Returns:
        Root, dependency, nested operation, and route effective values.

    Raises:
        ValueError: If normalized use values are incomplete.

    """
    uses: dict[str, JsonObject] = {}

    def visit(
        name: str,
        raw: Mapping[str, object],
        key: str,
        path: tuple[str, ...],
        binding: str | None = None,
    ) -> JsonObject:
        if key in uses:
            return uses[key]
        entry = catalog.graph.nodes[name]
        if normalized_uses is not None:
            raw = _normalized_values(normalized_uses, key)
        try:
            effective = _normalize(
                catalog, name, raw, complete=normalized_uses is not None
            )
        except MetricInputError as exc:
            raise ParameterUseError(exc, path, binding, entry.node.kind) from exc
        uses[key] = effective
        edges = [
            *catalog.graph.dependencies[name].values(),
            *catalog.graph.location_dependencies[name].values(),
        ]
        for edge in edges:
            if isinstance(edge, ResolvedDependency):
                target = edge.output.producer.node.name
                binding = f"inputs.{edge.input_name}"
            else:
                target = edge.producer.node.name
                binding = edge.binding_path
            visit(
                target,
                forwarded_parameters(effective, edge),
                use_key(name, effective, binding),
                (*path, target),
                binding,
            )
        return effective

    visit(node, values, "root", (node,))
    for item in inputs:
        for index, operation in enumerate(item.binding.operations):
            visit(
                operation, {}, route_key(item.name, index), (node, operation), item.name
            )
    if normalized_uses is not None and uses.keys() != normalized_uses.keys():
        message = "Unexpected normalized parameter uses"
        raise ValueError(message)
    return uses


def _normalize(
    catalog: CompiledCatalog, name: str, raw: Mapping[str, object], *, complete: bool
) -> JsonObject:
    entry = catalog.graph.nodes[name]
    if entry.node.parameters_schema is None:
        if raw:
            message = f"Node {name!r} has no parameters"
            raise ValueError(message)
        return {}
    plugin = catalog.implementations.get(entry.distribution)
    if plugin is None:
        message = f"Node {name!r} requires a live parameter model"
        raise ValueError(message)
    parameters = plugin.prepare_parameters(name, dict(raw))
    if complete:
        _require_complete(parameters, raw)
    return parameter_values(parameters)


def _require_complete(value: object, raw: object) -> None:
    """Check transport completeness using the model Pydantic actually selected.

    Raises:
        ValueError: If normalized model fields are missing.
    """
    if isinstance(value, BaseModel):
        if not isinstance(raw, Mapping) or type(value).model_fields.keys() - raw.keys():
            message = "Missing normalized parameter fields"
            raise ValueError(message)
        for name in type(value).model_fields:
            _require_complete(getattr(value, name), raw[name])
    elif isinstance(value, dict) and isinstance(raw, Mapping):
        for name, child in value.items():
            _require_complete(child, raw.get(name))
    elif isinstance(value, list) and isinstance(raw, list):
        for child, original in zip(value, raw, strict=True):
            _require_complete(child, original)


def _normalized_values(uses: Mapping[str, JsonObject], key: str) -> JsonObject:
    if key not in uses:
        message = f"Missing normalized parameter use: {key}"
        raise ValueError(message)
    return uses[key]
