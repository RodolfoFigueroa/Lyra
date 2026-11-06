"""Invocation declarations and serializable bindings owned by their caller."""

from __future__ import annotations

import re
from dataclasses import field
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass

from lyra.contracts.schema import check_json_value
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonValue


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Input:
    """Reference the owning caller's effective inputs, even inside nested bindings."""

    path: str = Field(pattern=r"^(parameters\.)?[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["input"] = field(default="input", kw_only=True)


def validate_bindings(
    bindings: dict[str, object], category: str, dependency_type: type
) -> None:
    """Validate direct-field names, reference categories and literal values.

    Raises:
        ValueError: If names, references or JSON literals are invalid.
        TypeError: If a location binding has an unsupported value type.
    """
    for name, value in bindings.items():
        path = f"{category}.{name}"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            msg = f"{path}: bindings must target direct input fields"
            raise ValueError(msg)
        if isinstance(value, Input):
            parameter = value.path.startswith("parameters.")
            if parameter != (category == "parameters"):
                msg = f"{path}: requires a {category[:-1]} reference"
                raise ValueError(msg)
        elif category == "locations":
            if not isinstance(value, dependency_type):
                msg = f"{path}: Location bindings require Input or LocationDependency"
                raise TypeError(msg)
        else:
            check_json_value(value, path)


class LiteralArgument(StrictBaseModel):
    """A complete JSON literal, distinct from a parameter reference."""

    kind: Literal["literal"] = "literal"
    value: JsonValue


class _BindingManifest(StrictBaseModel):
    # Typed nested values must still pass structural checks after mutation.
    model_config = ConfigDict(revalidate_instances="always")

    locations: dict[
        str,
        Annotated[Input | LocationDependencyManifest, Field(discriminator="kind")],
    ] = Field(default_factory=dict)
    parameters: dict[
        str, Annotated[Input | LiteralArgument, Field(discriminator="kind")]
    ] = Field(default_factory=dict)

    @field_validator("locations")
    @classmethod
    def validate_locations(
        cls, bindings: dict[str, Input | LocationDependencyManifest]
    ) -> dict[str, Input | LocationDependencyManifest]:
        """Return location bindings checked independently of the enabled graph."""
        validate_bindings(dict(bindings), "locations", LocationDependencyManifest)
        return bindings

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, bindings: dict[str, Input | LiteralArgument]
    ) -> dict[str, Input | LiteralArgument]:
        """Return parameter bindings without interpreting literal JSON objects."""
        validate_bindings(
            {
                name: value if isinstance(value, Input) else value.value
                for name, value in bindings.items()
            },
            "parameters",
            LocationDependencyManifest,
        )
        return bindings


class LocationDependencyManifest(_BindingManifest):
    """A nested operation invocation with a complete serializable binding tree."""

    kind: Literal["location_dependency"] = "location_dependency"
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class DependencyManifest(_BindingManifest):
    """Persisted table invocation with unambiguous argument bindings."""

    producer: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


LiteralArgument.model_rebuild(_types_namespace={"JsonValue": JsonValue})
