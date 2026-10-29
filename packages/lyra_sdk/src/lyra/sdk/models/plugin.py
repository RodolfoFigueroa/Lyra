"""Local node manifests and output metadata."""

import keyword
import re
from typing import Literal, Self

from lyra.sdk.dependencies import DependencyManifest
from lyra.sdk.locations import LocationProviderManifest
from lyra.sdk.models.output import LocationOutputMetadata, OutputMetadata
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
    location_output: LocationOutputMetadata | None = None
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    request_schema: JsonObject
    spatial_inputs: dict[str, SpatialInputKind] = Field(default_factory=dict)
    inputs: dict[str, DependencyManifest] = Field(default_factory=dict)
    outputs: dict[str, OutputMetadata] = Field(default_factory=dict)
    resources: dict[str, str] = Field(default_factory=dict)

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
        outputs: dict[str, str] = {}
        for node in self.nodes:
            for output in node.outputs:
                if output in outputs:
                    msg = (
                        f"Duplicate output {output!r} from {node.name!r} "
                        f"and {outputs[output]!r}"
                    )
                    raise ValueError(msg)
                if output in names and output != node.name:
                    msg = (
                        f"Output {output!r} from {node.name!r} "
                        "collides with another node"
                    )
                    raise ValueError(msg)
                outputs[output] = node.name
        self.nodes = sorted(self.nodes, key=lambda metric: metric.name)
        return self
