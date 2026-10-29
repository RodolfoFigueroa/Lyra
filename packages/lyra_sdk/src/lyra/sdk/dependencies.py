"""Invocation declarations and serializable bindings owned by their caller."""

from __future__ import annotations

import re
from dataclasses import field
from typing import Annotated, Literal

from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.schema import check_json_value
from lyra.sdk.types import JsonValue
from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Input:
    """Reference the owning caller's effective inputs, even inside nested bindings."""

    path: str = Field(pattern=r"^(parameters\.)?[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["input"] = field(default="input", kw_only=True)


def _validate_bindings(bindings: dict[str, object], category: str) -> None:
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
            if not isinstance(value, LocationDependency):
                msg = f"{path}: Location bindings require Input or LocationDependency"
                raise TypeError(msg)
        else:
            check_json_value(value, path)


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _Bindings:
    locations: dict[str, object] = field(default_factory=dict, kw_only=True)
    parameters: dict[str, object] = field(default_factory=dict, kw_only=True)

    @field_validator("locations")
    @classmethod
    def validate_locations(cls, bindings: dict[str, object]) -> dict[str, object]:
        """Return locally validated location bindings."""
        _validate_bindings(bindings, "locations")
        return bindings

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, bindings: dict[str, object]) -> dict[str, object]:
        """Return locally validated whole-field parameter bindings."""
        _validate_bindings(bindings, "parameters")
        return bindings


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class LocationDependency(_Bindings):
    """Invoke an operation; nested references belong to the declaration's caller.

    Explicit bindings override matching-name forwarding from that caller.
    """

    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Dependency(_Bindings):
    """Invoke a table output with explicit bindings or matching-name forwarding."""

    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class LiteralArgument(StrictBaseModel):
    """A complete JSON literal, distinct from a parameter reference."""

    kind: Literal["literal"] = "literal"
    value: JsonValue


class _BindingManifest(StrictBaseModel):
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
        _validate_bindings(
            {
                name: value if isinstance(value, Input) else value.to_declaration()
                for name, value in bindings.items()
            },
            "locations",
        )
        return bindings

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, bindings: dict[str, Input | LiteralArgument]
    ) -> dict[str, Input | LiteralArgument]:
        """Return parameter bindings without interpreting literal JSON objects."""
        _validate_bindings(
            {
                name: value if isinstance(value, Input) else value.value
                for name, value in bindings.items()
            },
            "parameters",
        )
        return bindings


def _manifest_bindings(dependency: _Bindings) -> dict[str, object]:
    return {
        "locations": {
            name: LocationDependencyManifest.from_declaration(value)
            if isinstance(value, LocationDependency)
            else value
            for name, value in dependency.locations.items()
        },
        "parameters": {
            name: value
            if isinstance(value, Input)
            else LiteralArgument.model_validate({"value": value})
            for name, value in dependency.parameters.items()
        },
    }


class LocationDependencyManifest(_BindingManifest):
    """A nested operation invocation with a complete serializable binding tree."""

    kind: Literal["location_dependency"] = "location_dependency"
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*$")

    @classmethod
    def from_declaration(
        cls, dependency: LocationDependency
    ) -> LocationDependencyManifest:
        """Return metadata preserving references and literal values."""
        return cls.model_validate(
            {"operation": dependency.operation, **_manifest_bindings(dependency)}
        )

    def to_declaration(self) -> LocationDependency:
        """Return the equivalent declaration."""
        return LocationDependency(
            self.operation,
            locations={
                name: value if isinstance(value, Input) else value.to_declaration()
                for name, value in self.locations.items()
            },
            parameters={
                name: value if isinstance(value, Input) else value.value
                for name, value in self.parameters.items()
            },
        )


class DependencyManifest(_BindingManifest):
    """Persisted table invocation with unambiguous argument bindings."""

    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")

    @classmethod
    def from_declaration(cls, dependency: Dependency) -> DependencyManifest:
        """Return serializable metadata without interpreting literal objects."""
        return cls.model_validate(
            {"output": dependency.output, **_manifest_bindings(dependency)}
        )


LiteralArgument.model_rebuild(_types_namespace={"JsonValue": JsonValue})
