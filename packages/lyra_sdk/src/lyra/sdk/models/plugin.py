"""Local node manifests and output metadata."""

import keyword
import re
from typing import Literal, Self

from lyra.sdk.dependencies import DependencyManifest
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.locations import LocationProviderManifest
from lyra.sdk.models.output import (
    FixedLocationScope,
    LocationOperationOutputMetadata,
    OutputMetadata,
)
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.schema import check_request_schema
from lyra.sdk.types import JsonObject
from pydantic import Field, field_validator, model_validator

SpatialInputKind = Literal["location"]
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


class NodeManifest(StrictBaseModel):
    """One local node with its complete unresolved request schema."""

    kind: Literal["metric", "location_operation"] = "metric"
    private: bool = False
    canonical: bool = False
    location_output: LocationOperationOutputMetadata | None = None
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    request_schema: JsonObject
    spatial_inputs: dict[str, SpatialInputKind] = Field(default_factory=dict)
    inputs: dict[str, DependencyManifest] = Field(default_factory=dict)
    outputs: dict[str, OutputMetadata] = Field(default_factory=dict)
    location_contracts: dict[str, LocationContract] = Field(default_factory=dict)
    output_contract: LocationContract | None = None
    resources: dict[str, str] = Field(default_factory=dict)
    family: str | None = None
    edition: str | None = Field(default=None, min_length=1)
    edition_metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_canonical(self) -> Self:
        """Validate the shape of an opted-in routing operation.

        Returns:
            The validated declaration.

        Raises:
            ValueError: If canonical status is used on an ineligible node.
        """
        if self.canonical and (
            self.kind != "location_operation" or len(self.spatial_inputs) != 1
        ):
            message = "Canonical operations require exactly one named location input"
            raise ValueError(message)
        return self

    @property
    def is_public_metric(self) -> bool:
        """Whether this node is an ordinary public entry point."""
        return self.kind == "metric" and not self.private

    @field_validator("outputs")
    @classmethod
    def validate_outputs(
        cls, outputs: dict[str, OutputMetadata]
    ) -> dict[str, OutputMetadata]:
        """Return outputs with valid public identifiers."""
        for name in outputs:
            validate_metric_name(name)
        return outputs

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Return a valid, nonreserved metric identifier."""
        return validate_metric_name(name)

    @field_validator("description")
    @classmethod
    def validate_description(cls, description: str) -> str:
        """Return a nonblank description.

        Raises:
            ValueError: If the description contains only whitespace.
        """
        if not description.strip():
            msg = "Metric description must not be blank."
            raise ValueError(msg)
        return description

    @model_validator(mode="after")
    def validate_concrete_contracts(self) -> Self:
        """Validate concrete input and output declarations.

        Returns:
            The validated node.

        Raises:
            ValueError: If declarations disagree with node structure.
        """
        if self.kind != "metric" and self.private:
            message = "Only metrics may be private"
            raise ValueError(message)
        if self.family is not None:
            validate_metric_name(self.family)
        if self.edition is not None and (
            self.family is None or not self.edition.strip()
        ):
            message = "Named editions require an explicit family and nonblank edition"
            raise ValueError(message)
        if self.kind != "metric" and (
            self.family is not None or self.edition is not None
        ):
            message = "Metric family registration applies only to metrics"
            raise ValueError(message)
        if self.location_contracts.keys() != self.spatial_inputs.keys():
            message = "Concrete contracts must cover every named location input"
            raise ValueError(message)
        if self.kind == "location_operation" and self.output_contract is None:
            message = "Location operations require a concrete output contract"
            raise ValueError(message)
        if self.output_contract is not None:
            output = self.location_output
            family = self.output_contract.family
            if output is None or (output.id_column, output.output) != (
                family.structure.id_column,
                family.structure.output,
            ):
                message = "Concrete output contract differs from operation schema"
                raise ValueError(message)
            if not isinstance(output.scope, FixedLocationScope) or (
                output.scope.collection,
                output.scope.unit_type,
            ) != (family.collection, family.unit_type):
                message = "Concrete output contract differs from operation scope"
                raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Check spatial metadata and the self-contained root request contract.

        Returns:
            This validated metric.

        Raises:
            ValueError: If root fields, spatial conventions, or schema are invalid.
        """
        if self.kind == "metric":
            if not self.outputs or self.location_output is not None:
                msg = "Metrics require table outputs and no location output"
                raise ValueError(msg)
        elif self.outputs or self.location_output is None:
            msg = "Location operations require one location output and no table outputs"
            raise ValueError(msg)
        if self.inputs.keys() & self.resources.keys():
            msg = "Resource arguments must be separate from dependency inputs"
            raise ValueError(msg)
        if self.spatial_inputs.keys() & (self.inputs.keys() | self.resources.keys()):
            msg = "Location arguments conflict with dependency or resource inputs"
            raise ValueError(msg)
        for name in (
            self.inputs.keys() | self.resources.keys() | self.spatial_inputs.keys()
        ):
            if (
                not name.isidentifier()
                or keyword.iskeyword(name)
                or name
                in {
                    "parameters",
                    "context",
                }
            ):
                msg = f"Node {self.name!r}: invalid receiving input {name!r}"
                raise ValueError(msg)
        check_request_schema(self.request_schema)
        properties = self.request_schema.get("properties")
        expected = set(self.spatial_inputs)
        if isinstance(properties, dict) and "parameters" in properties:
            expected.add("parameters")
        required = self.request_schema.get("required", [])
        valid_fields = isinstance(properties, dict) and set(properties) == expected
        valid_required = (
            isinstance(required, list)
            and required == list(dict.fromkeys(required))
            and set(required) == expected
        )
        if (
            self.request_schema.get("type") != "object"
            or self.request_schema.get("additionalProperties") is not False
            or not valid_fields
            or not valid_required
        ):
            msg = (
                "Request schema must require exactly the declared "
                "spatial/parameter fields."
            )
            raise ValueError(msg)
        return self


class PluginManifest(StrictBaseModel):
    """Plugin ownership and explicitly registered local nodes."""

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
                    *node.location_contracts.values(),
                    *([node.output_contract] if node.output_contract else []),
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
