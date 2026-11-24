"""Local node manifests and output metadata."""

import keyword
import re
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from lyra.contracts.dependencies import (
    Dependency,
    LocationDependency,
    describe_dependency,
    validate_bindings,
)
from lyra.contracts.locations import LocationContract
from lyra.contracts.metric_scope import (
    CachePolicy,
    OutputScope,
    primary_input,
    validate_cache,
)
from lyra.contracts.operations import OPERATION_MAPPING_ADAPTER
from lyra.contracts.origins import Origin
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.providers import ProviderDeclaration
from lyra.contracts.revisions import ImplementationRevision
from lyra.contracts.schema import build_node_request_schema, check_parameter_schema
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject

_PUBLIC_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FACTORY_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


def validate_metric_name(name: str) -> str:
    """Validate a public metric identifier.

    Returns:
        The identifier unchanged.

    Raises:
        ValueError: If the name is invalid or reserved.
    """
    if not _PUBLIC_NAME_PATTERN.fullmatch(name) or name.startswith("lyra_"):
        msg = "Metric name must match ^[a-z][a-z0-9_]*$ and not start with 'lyra_'."
        raise ValueError(msg)
    return name


class PluginInfo(StrictBaseModel):
    """Identity captured from a plugin project."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class LocationInputDeclaration(StrictBaseModel):
    """One named location argument, its native contract and selection bindings."""

    contract: LocationContract
    provider: ProviderDeclaration | None = None
    dependency: LocationDependency | None = None
    description: str | None = Field(default=None, min_length=1)

    @field_serializer("dependency")
    @staticmethod
    def describe_requirement(value: LocationDependency | None) -> dict | None:
        """Return provider requirements without inapplicable invocation bindings."""
        if value is None:
            return None
        described = describe_dependency(value)
        if value.provider is not None:
            described.pop("locations")
        return described

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        """Reject blank descriptions.

        Returns:
            The supplied description.

        Raises:
            ValueError: If the declaration is invalid.
        """
        if value is not None and not value.strip():
            message = "Location input description must not be blank"
            raise ValueError(message)
        return value


class _NodeManifest(StrictBaseModel):
    """Shared serializable declarations; derived views are detached."""

    name: str = Field(min_length=1)
    code_revision: ImplementationRevision | None = None
    description: str = Field(min_length=1)
    parameters_schema: JsonObject | None = None
    location_inputs: dict[str, LocationInputDeclaration] = Field(default_factory=dict)
    inputs: dict[str, Dependency] = Field(default_factory=dict)
    resources: dict[str, str] = Field(default_factory=dict)

    @field_validator("inputs", mode="before")
    @classmethod
    def capture_inputs(cls, values: object) -> object:
        """Return owned live declarations after validating their current bindings.

        Raises:
            TypeError: If an input is an inspection description.
        """
        if isinstance(values, dict):
            for value in values.values():
                if not isinstance(value, Dependency):
                    message = "Node inputs require live Dependency declarations"
                    raise TypeError(message)
                validate_bindings(dict(value.locations), "locations")
                validate_bindings(value.parameters, "parameters")
            return deepcopy(values)
        return values

    @property
    def request_schema(self) -> dict[str, Any]:
        """Generate a detached complete request schema without runtime code."""
        return build_node_request_schema(
            self.name,
            self.parameters_schema,
            {
                name: value.description
                for name, value in self.location_inputs.items()
                if value.dependency is None or value.dependency.provider is not None
            },
        )

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Return a valid public identifier."""
        return validate_metric_name(name)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        """Reject blank descriptions.

        Returns:
            The supplied description.

        Raises:
            ValueError: If the declaration is invalid.
        """
        if not value.strip():
            message = "Metric description must not be blank."
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Validate argument names and the self-contained parameter contract.

        Returns:
            This node with validated argument and parameter declarations.

        Raises:
            ValueError: If the declaration is invalid.
        """
        if self.inputs.keys() & self.resources.keys():
            message = "Resource arguments must be separate from dependency inputs"
            raise ValueError(message)
        if self.location_inputs.keys() & (self.inputs.keys() | self.resources.keys()):
            message = "Location arguments conflict with dependency or resource inputs"
            raise ValueError(message)
        for name in (
            self.inputs.keys() | self.resources.keys() | self.location_inputs.keys()
        ):
            if (
                not name.isidentifier()
                or keyword.iskeyword(name)
                or name in {"parameters", "context"}
            ):
                message = f"Node {self.name!r}: invalid receiving input {name!r}"
                raise ValueError(message)
        if self.parameters_schema is not None:
            check_parameter_schema(self.parameters_schema)
        return self


class MetricManifest(_NodeManifest):
    """A table-producing metric selected by its registered name."""

    kind: Literal["metric"] = "metric"
    scope: OutputScope = "unpartitioned"
    primary_input: str | None = None
    cache: CachePolicy = "off"
    origins: Annotated[frozenset[Origin], Field(strict=False)] = frozenset()
    private: bool = False
    outputs: dict[str, OutputMetadata] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cache_scope(self) -> Self:
        """Return a manifest with a cache strategy eligible for its output scope.

        Raises:
            ValueError: If ownership or cache eligibility is inconsistent.
        """
        owner = primary_input(
            {
                name: value.dependency
                for name, value in self.location_inputs.items()
                if value.dependency is not None
            }
        )
        scope = "location" if owner is not None else "unpartitioned"
        if self.primary_input != owner or self.scope != scope:
            message = "Metric scope and primary input must match declared ownership"
            raise ValueError(message)
        validate_cache(self.cache, scope)
        return self

    @property
    def is_public_metric(self) -> bool:
        """Whether the metric is a public entry point."""
        return not self.private

    @field_validator("outputs")
    @classmethod
    def validate_outputs(
        cls, outputs: dict[str, OutputMetadata]
    ) -> dict[str, OutputMetadata]:
        """Validate output identifiers.

        Returns:
            The output declarations with valid identifiers.
        """
        for name in outputs:
            validate_metric_name(name)
        return outputs


class LocationOperationManifest(_NodeManifest):
    """An operation producing locations under one concrete output contract."""

    kind: Literal["location_operation"] = "location_operation"
    canonical: bool = False
    target_provider: str | None = None
    has_target_selector: bool = False
    scope: Literal["location"] = "location"
    primary_input: str | None = None
    cache: CachePolicy = "off"
    output_contract: LocationContract

    @property
    def result_schema(self) -> dict:
        """The fixed mapping result's JSON validation contract."""
        return OPERATION_MAPPING_ADAPTER.json_schema()

    @property
    def is_public_metric(self) -> bool:
        """Operations are not public metric entry points."""
        return False

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        """Validate canonical operation shape.

        Returns:
            This operation with valid routing and input declarations.

        Raises:
            ValueError: If the declaration is invalid.
        """
        validate_cache(self.cache, "location")
        if self.canonical and len(self.location_inputs) != 1:
            message = "Canonical operations require exactly one named location input"
            raise ValueError(message)
        return self


NodeManifest = Annotated[
    MetricManifest | LocationOperationManifest, Field(discriminator="kind")
]
NODE_MANIFEST_ADAPTER = TypeAdapter(NodeManifest)


class PluginManifest(StrictBaseModel):
    """Plugin ownership and explicitly registered local nodes."""

    route_preferences: tuple[tuple[str, ...], ...] = ()
    plugin: PluginInfo
    factory: str
    resources: dict[str, dict[str, object]] = Field(default_factory=dict)
    nodes: list[NodeManifest] = Field(default_factory=list)
    providers: tuple[ProviderDeclaration, ...] = ()

    @field_validator("factory")
    @classmethod
    def validate_factory(cls, factory: str) -> str:
        """Return a valid module:attribute factory reference.

        Raises:
            ValueError: If the reference has unsupported syntax.
        """
        if not _FACTORY_PATTERN.fullmatch(factory):
            msg = "Factory must use module:attribute syntax."
            raise ValueError(msg)
        return factory

    @model_validator(mode="after")
    def validate_provider_contracts(self) -> Self:
        """Check that node declarations agree with registered providers.

        Returns:
            The validated manifest.

        Raises:
            ValueError: If concrete declarations disagree.
        """
        if self.providers:
            registered = {
                item.contract.reference: item.contract for item in self.providers
            }
            for node in self.nodes:
                for contract in [
                    *(value.contract for value in node.location_inputs.values()),
                    *(
                        [node.output_contract]
                        if node.kind == "location_operation"
                        else []
                    ),
                ]:
                    if (
                        contract.reference in registered
                        and contract != registered[contract.reference]
                    ):
                        message = "Node and provider concrete contracts disagree"
                        raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_nodes(self) -> Self:
        """Require unique node names and store them in deterministic order.

        Returns:
            This manifest with sorted nodes.

        Raises:
            ValueError: If node names repeat.
        """
        if not self.nodes and not self.providers and not self.resources:
            msg = "Plugin requires at least one node or location provider"
            raise ValueError(msg)
        if any(provider.resource not in self.resources for provider in self.providers):
            msg = "Location provider must reference a registered resource"
            raise ValueError(msg)
        provider_ids = [provider.id for provider in self.providers]
        if len(set(provider_ids)) != len(provider_ids):
            message = "Provider IDs must be unique"
            raise ValueError(message)
        names = [metric.name for metric in self.nodes]
        if len(names) != len(set(names)):
            msg = "Plugin node names must be unique."
            raise ValueError(msg)
        self.nodes = sorted(self.nodes, key=lambda metric: metric.name)
        return self


class PluginInspection(StrictBaseModel):
    """Serializable plugin inspection data, never executable planner input."""

    route_preferences: tuple[tuple[str, ...], ...] = ()
    plugin: PluginInfo
    factory: str
    resources: dict[str, dict[str, object]] = Field(default_factory=dict)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    providers: tuple[ProviderDeclaration, ...] = ()
