"""Prepare effective values for declaration uses without resolving locations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.runtime.planning.graph import ResolvedDependency, ResolvedLocationDependency
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lyra.contracts.accepted import AcceptedSource, PreparedInput
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


def use_key(
    owner: str, values: JsonObject, binding: str, *, scope: str = "root"
) -> str:
    """Return a stable receiving-use key within its declaring caller scope."""
    if scope != "root":
        scope = hashlib.sha256(scope.encode()).hexdigest()
    return json.dumps([scope, owner, values, binding], sort_keys=True, allow_nan=False)


def receiving_key(scope: str, binding: str, name: str) -> str:
    """Return a receiving-route key in its original caller's invocation scope."""
    return json.dumps([scope, binding, name])


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
    sources = parameter_sources(source, edge)
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    values = {}
    for target, name in sources.items():
        if name not in source:
            raise MetricInputError(
                producer.name, f"parameters.{name}", "missing referenced input"
            )
        values[target] = source[name]
    for name, binding in edge.dependency.parameters.items():
        if not isinstance(binding, Input):
            values[name] = cast("JsonValue", binding)
    return deepcopy(values)


def parameter_sources(
    source: JsonObject,
    edge: ResolvedDependency | ResolvedLocationDependency,
) -> dict[str, str]:
    """Map receiving names to caller names after explicit binding overrides.

    Returns:
        Direct whole-parameter connections, excluding literals and defaults.
    """
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    fields = cast("dict", (producer.parameters_schema or {}).get("properties", {}))
    sources = {name: name for name in fields if name in source}
    for name, binding in edge.dependency.parameters.items():
        if isinstance(binding, Input):
            sources[name] = binding.path.split(".")[1]
        else:
            sources.pop(name, None)
    return sources


@dataclass(frozen=True)
class PreparedParameters:
    """Complete effective values and canonical routes for distinct receiving uses."""

    uses: dict[str, JsonObject]
    routes: dict[str, AcceptedSource]


def prepare_parameter_uses(
    catalog: CompiledCatalog,
    node: str,
    values: JsonObject,
    inputs: Sequence[PreparedInput] = (),
    *,
    normalized_uses: Mapping[str, JsonObject] | None = None,
) -> PreparedParameters:
    """Normalize selected uses through live models without acquiring records.

    Returns:
        Complete effective values and receiving routes in caller scope.

    Raises:
        ValueError: If normalized use values are incomplete or unexpected.
    """
    preparation = _ParameterPreparation(catalog, normalized_uses)
    preparation.visit(node, values, "root", (node,))
    for item in inputs:
        for index, operation in enumerate(item.binding.operations):
            preparation.visit(
                operation, {}, route_key(item.name, index), (node, operation), item.name
            )
    if (
        normalized_uses is not None
        and preparation.uses.keys() != normalized_uses.keys()
    ):
        message = "Unexpected normalized parameter uses"
        raise ValueError(message)
    return PreparedParameters(preparation.uses, preparation.routes)


@dataclass
class _ParameterPreparation:
    catalog: CompiledCatalog
    normalized: Mapping[str, JsonObject] | None
    uses: dict[str, JsonObject] = field(default_factory=dict)
    routes: dict[str, AcceptedSource] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)

    def visit(
        self,
        name: str,
        raw: Mapping[str, object],
        key: str,
        path: tuple[str, ...],
        binding: str | None = None,
    ) -> None:
        if name in self.active:
            message = f"Execution plan cycle: {' -> '.join(path)}"
            raise ValueError(message)
        if key in self.uses:
            return
        self.active.add(name)
        forwarded = raw
        accepted = None
        if self.normalized is not None:
            accepted = _normalized_values(self.normalized, key)
            raw = accepted
        try:
            effective = _normalize(
                self.catalog, name, raw, complete=self.normalized is not None
            )
        except MetricInputError as exc:
            kind = self.catalog.graph.nodes[name].node.kind
            raise ParameterUseError(exc, path, binding, kind) from exc
        _validate_accepted_values(self.catalog, name, accepted, forwarded, key)
        self.uses[key] = effective
        for edge in (
            *self.catalog.graph.dependencies[name].values(),
            *self.catalog.graph.location_dependencies[name].values(),
        ):
            self.edge((name, key), effective, edge, path)
        self.active.remove(name)

    def edge(
        self,
        owner: tuple[str, str],
        effective: JsonObject,
        edge: ResolvedDependency | ResolvedLocationDependency,
        path: tuple[str, ...],
    ) -> None:
        name, key = owner
        if isinstance(edge, ResolvedDependency):
            target = edge.output.producer.node.name
            binding = f"inputs.{edge.input_name}"
        else:
            target = edge.producer.node.name
            binding = edge.binding_path
        self.visit(
            target,
            forwarded_parameters(effective, edge),
            use_key(name, effective, binding, scope=key),
            (*path, target),
            binding,
        )
        for receiving in self.catalog.graph.nodes[target].node.location_inputs:
            route = self.catalog.dependency_routes.get((name, binding, receiving))
            if route is None:
                continue
            route_use = receiving_key(key, binding, receiving)
            self.routes[route_use] = route
            for index, operation in enumerate(route.operations):
                self.visit(
                    operation,
                    {},
                    route_key(route_use, index),
                    (*path, target, operation),
                    f"{binding}.locations.{receiving}",
                )


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
    parameters = plugin.prepare_parameters(name, deepcopy(dict(raw)))
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


def _validate_accepted_values(
    catalog: CompiledCatalog,
    name: str,
    accepted: JsonObject | None,
    forwarded: Mapping[str, object],
    key: str,
) -> None:
    if accepted is None or all(accepted.get(k) == v for k, v in forwarded.items()):
        return
    expected = _normalize(catalog, name, accepted | dict(forwarded), complete=False)
    if expected != accepted:
        message = f"Effective parameter bindings disagree with acceptance: {key}"
        raise ValueError(message)
