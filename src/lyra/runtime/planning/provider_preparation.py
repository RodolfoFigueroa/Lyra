"""Acquisition-free native request preparation for managed runtime integration."""

from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated

from pydantic import ConfigDict, Field

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.locations import LocationSelection as ProviderSelection
from lyra.contracts.origins import Origin
from lyra.contracts.revisions import CacheRevision
from lyra.contracts.schema import check_json_value
from lyra.contracts.strict import StrictBaseModel
from lyra.runtime.planning.node_declarations import (
    CompiledNode,
    NodeDeclaration,
    compile_node_declarations,
)
from lyra.runtime.planning.provider_registry import (
    RegisteredProvider,
    compile_provider_registry,
)
from lyra.sdk.parameters import parameter_values
from lyra.sdk.plugin import PluginDefinition


class ProviderRequest(StrictBaseModel):
    """Separate selection and parameter sections with omission defaults."""

    model_config = ConfigDict(strict=True)
    metric: Annotated[str, Field(min_length=1)]
    inputs: dict[str, ProviderSelection] = Field(default_factory=dict)
    parameters: dict[str, object] = Field(default_factory=dict)
    cache_revision: CacheRevision | None = None


@dataclass(frozen=True)
class PreparedProviderInput:
    """Detached selection retaining scope even when no records are needed."""

    name: str
    ids: frozenset[str]
    provider: RegisteredProvider


@dataclass(frozen=True)
class PreparedNodeUse:
    """One invocation's effective parameters and its own direct origins."""

    node: str
    path: tuple[str, ...]
    parameters: Mapping[str, object]
    origins: frozenset[Origin]


@dataclass(frozen=True)
class PreparedProviderRequest:
    """Selected metadata and dependency closure, without acquisition claims."""

    metric: str
    cache_revision: str | None
    inputs: tuple[PreparedProviderInput, ...]
    uses: tuple[PreparedNodeUse, ...]
    providers: Mapping[str, RegisteredProvider]


def prepare_provider_request(
    definitions: Mapping[str, PluginDefinition],
    declarations: Sequence[NodeDeclaration],
    request: Mapping[str, object],
) -> PreparedProviderRequest:
    """Validate native selections and capture live declaration identities.

    This internal preparation entry point supports direct matching providers.
    Public transport and existing automatic routes remain on their current path
    until runtime integration. No cache, resource or handler is consulted here.

    Returns:
        Detached selections and metadata for every selected declaration use.

    Raises:
        ValueError: If the request, parameters or declarations are invalid.
    """
    parsed = ProviderRequest.model_validate(deepcopy(dict(request)))
    nodes = compile_node_declarations(definitions, declarations)
    providers = compile_provider_registry(definitions)
    if parsed.metric not in nodes or nodes[parsed.metric].declaration.kind != "metric":
        msg = f"Unknown metric {parsed.metric!r}"
        raise ValueError(msg)
    root = nodes[parsed.metric]
    required = {
        name: dependency.provider
        for name, dependency in root.declaration.inputs.items()
        if isinstance(dependency, LocationDependency)
        and dependency.provider is not None
    }
    validate_provider_selections(parsed.inputs, required)
    inputs = []
    for name, selection in parsed.inputs.items():
        if selection.provider not in providers:
            msg = f"Unknown provider {selection.provider!r}"
            raise ValueError(msg)
        inputs.append(
            PreparedProviderInput(
                name, frozenset(selection.ids), providers[selection.provider]
            )
        )
    collector = _Collector(nodes, providers)
    collector.visit(parsed.metric, parsed.parameters, (parsed.metric,))
    return PreparedProviderRequest(
        parsed.metric,
        parsed.cache_revision,
        tuple(inputs),
        tuple(collector.uses),
        MappingProxyType(collector.providers),
    )


def validate_provider_selections(
    selections: Mapping[str, ProviderSelection],
    required: Mapping[str, str],
    accepted: Mapping[str, Collection[str]] | None = None,
) -> None:
    """Validate caller-supplied provider inputs before any acquisition.

    Derived input names are absent from required. Existing routing can supply
    accepted source IDs; direct preparation admits only the required provider.

    Raises:
        ValueError: If input names or selected providers are unsupported.
    """
    if selections.keys() != required.keys():
        message = f"Expected inputs {sorted(required)}"
        raise ValueError(message)
    for name, selection in selections.items():
        supported = accepted[name] if accepted is not None else (required[name],)
        if selection.provider not in supported:
            message = (
                f"Input {name!r} requires provider {required[name]!r} "
                "or a declared route"
            )
            raise ValueError(message)


class _Collector:
    def __init__(
        self,
        nodes: Mapping[str, CompiledNode],
        providers: Mapping[str, RegisteredProvider],
    ) -> None:
        self.nodes = nodes
        self.registry = providers
        self.providers: dict[str, RegisteredProvider] = {}
        self.uses: list[PreparedNodeUse] = []

    def visit(
        self, name: str, raw: Mapping[str, object], path: tuple[str, ...]
    ) -> None:
        node = self.nodes[name]
        check_json_value(dict(raw), "parameters")
        if node.parameters is None:
            if raw:
                msg = f"Node {name!r} has no parameters"
                raise ValueError(msg)
            effective = {}
        else:
            effective = parameter_values(
                node.parameters.model_validate(deepcopy(dict(raw)), extra="forbid")
            )
        self.uses.append(
            PreparedNodeUse(
                name, path, MappingProxyType(deepcopy(effective)), node.origins
            )
        )
        for provider in node.locations.values():
            self.providers[provider.scope.provider_id] = provider
        if node.declaration.target is not None:
            target = node.declaration.target
            self.providers[target] = self.registry[target]
        for key, dependency in node.declaration.inputs.items():
            self.binding(dependency, effective, (*path, key))

    def binding(
        self,
        dependency: Dependency | LocationDependency,
        caller: Mapping[str, object],
        path: tuple[str, ...],
    ) -> None:
        if (
            isinstance(dependency, LocationDependency)
            and dependency.provider is not None
        ):
            self.providers[dependency.provider] = self.registry[dependency.provider]
            return
        target = (
            dependency.producer
            if isinstance(dependency, Dependency)
            else str(dependency.operation)
        )
        model = self.nodes[target].parameters
        fields = model.model_fields if model else {}
        values = {name: caller[name] for name in fields if name in caller}
        for name, value in dependency.parameters.items():
            if isinstance(value, Input):
                field = value.path.removeprefix("parameters.")
                if field not in caller:
                    msg = f"Missing referenced parameter {value.path!r}"
                    raise ValueError(msg)
                values[name] = caller[field]
            else:
                values[name] = value
        self.visit(target, values, (*path, target))
        for key, location in dependency.locations.items():
            if isinstance(location, LocationDependency):
                # Nested bindings read the declaring caller, not the receiving node.
                self.binding(location, caller, (*path, key))
