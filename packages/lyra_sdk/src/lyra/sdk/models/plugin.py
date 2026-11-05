"""Local node manifests and output metadata."""

import keyword
import re
from typing import Annotated, Any, Literal, Self

from lyra.sdk.dependencies import DependencyManifest
from lyra.sdk.location_contracts import LocationContract, LocationContractReference
from lyra.sdk.locations import LocationProviderManifest
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.schema import build_node_request_schema, check_parameter_schema
from lyra.sdk.types import JsonObject
from pydantic import Field, TypeAdapter, field_validator, model_validator

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
    description: str | None = Field(default=None, min_length=1)
    source_bindings: tuple[LocationContractReference, ...] = ()

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
    description: str = Field(min_length=1)
    parameters_schema: JsonObject | None = None
    location_inputs: dict[str, LocationInputDeclaration] = Field(default_factory=dict)
    inputs: dict[str, DependencyManifest] = Field(default_factory=dict)
    resources: dict[str, str] = Field(default_factory=dict)

    @property
    def request_schema(self) -> dict[str, Any]:
        """Generate a detached complete request schema without runtime code."""
        return build_node_request_schema(
            self.name,
            self.parameters_schema,
            {name: value.description for name, value in self.location_inputs.items()},
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
    """A table-producing metric, optionally registered as a family edition."""

    kind: Literal["metric"] = "metric"
    private: bool = False
    outputs: dict[str, OutputMetadata] = Field(min_length=1)
    family: str | None = None
    edition: str | None = Field(default=None, min_length=1)
    edition_metadata: JsonObject = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def validate_edition(self) -> Self:
        """Require an explicit family for named editions.

        Returns:
            This metric with a valid family and edition.

        Raises:
            ValueError: If the declaration is invalid.
        """
        if self.family is not None:
            validate_metric_name(self.family)
        if self.edition is not None and (
            self.family is None or not self.edition.strip()
        ):
            message = "Named editions require an explicit family and nonblank edition"
            raise ValueError(message)
        return self


class LocationOperationManifest(_NodeManifest):
    """An operation producing locations under one concrete output contract."""

    kind: Literal["location_operation"] = "location_operation"
    canonical: bool = False
    output_contract: LocationContract

    @property
    def is_public_metric(self) -> bool:
        """Operations are not public metric entry points."""
        return False

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        """Validate canonical shape and metric-only source overrides.

        Returns:
            This operation with valid routing and input declarations.

        Raises:
            ValueError: If the declaration is invalid.
        """
        if self.canonical and len(self.location_inputs) != 1:
            message = "Canonical operations require exactly one named location input"
            raise ValueError(message)
        if any(value.source_bindings for value in self.location_inputs.values()):
            message = "Source bindings apply only to metrics"
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
    location_provider: LocationProviderManifest | None = None

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
        if self.location_provider is not None:
            registered = {
                item.reference: item for item in self.location_provider.contracts
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
        if not self.nodes and self.location_provider is None:
            msg = "Plugin requires at least one node or location provider"
            raise ValueError(msg)
        if (
            self.location_provider is not None
            and self.location_provider.resource not in self.resources
        ):
            msg = "Location provider must reference a registered resource"
            raise ValueError(msg)
        names = [metric.name for metric in self.nodes]
        if len(names) != len(set(names)):
            msg = "Plugin node names must be unique."
            raise ValueError(msg)
        self.nodes = sorted(self.nodes, key=lambda metric: metric.name)
        return self
