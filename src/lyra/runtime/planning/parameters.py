"""Prepare effective values for declaration uses without resolving locations."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.contracts.value_identity import same_json
from lyra.runtime.planning.graph import ResolvedDependency, ResolvedLocationDependency
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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


def use_key(scope: str, binding: str) -> str:
    """Return the key for an edge within its declaring invocation."""
    path = [] if scope == "root" else json.loads(scope)
    return json.dumps([*path, ["edge", binding]], separators=(",", ":"))


def receiving_key(scope: str, binding: str, name: str) -> str:
    """Return the key for an input routed into a dependency."""
    path = json.loads(use_key(scope, binding))
    return json.dumps([*path, ["input", name]], separators=(",", ":"))


def route_key(name: str, index: int) -> str:
    """Return the key for one automatic hop at a root or dependency input."""
    path = json.loads(name) if name.startswith("[") else [["input", name]]
    return json.dumps([*path, ["hop", index]], separators=(",", ":"))


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
    if normalized_uses is not None:
        if "root" in normalized_uses:
            message = "Unexpected normalized root parameter use"
            raise ValueError(message)
        normalized_uses = {"root": values, **normalized_uses}
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
        if self.normalized is not None:
            raw = _normalized_values(self.normalized, key)
        try:
            effective = _normalize(
                self.catalog, name, raw, complete=self.normalized is not None
            )
        except MetricInputError as exc:
            kind = self.catalog.graph.nodes[name].node.kind
            raise ParameterUseError(exc, path, binding, kind) from exc
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
            # Accepted values include defaults inside partially forwarded objects.
            # The pinned plan digest covers both the declarations and all uses.
            forwarded_parameters(effective, edge) if self.normalized is None else {},
            use_key(key, binding),
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
    parameters = plugin.prepare_parameters(name, raw)
    values = parameter_values(parameters)
    if same_json(values, raw):
        return values
    if complete:
        message = f"Missing normalized fields or changed effective values for {name!r}"
        raise ValueError(message)
    # Defaults and coercion can produce JSON that selects a different union member.
    replayed = plugin.prepare_parameters(name, values)
    if not same_json(parameter_values(replayed), values):
        raise MetricInputError(
            name, "parameters", "Effective values change on revalidation"
        )
    return values


def _normalized_values(uses: Mapping[str, JsonObject], key: str) -> JsonObject:
    if key not in uses:
        message = f"Missing normalized parameter use: {key}"
        raise ValueError(message)
    return uses[key]
