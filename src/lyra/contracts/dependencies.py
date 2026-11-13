"""Invocation declarations and serializable bindings owned by their caller."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import field
from typing import Literal, Self, cast

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.dataclasses import dataclass

from lyra.contracts.schema import check_json_value


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Input:
    """Reference the owning caller's effective inputs, even inside nested bindings."""

    path: str = Field(pattern=r"^(parameters\.)?[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["input"] = field(default="input", kw_only=True)


def validate_bindings(bindings: dict[str, object], category: str) -> None:
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
            if not isinstance(value, LocationDependency):
                msg = f"{path}: Location bindings require Input or LocationDependency"
                raise TypeError(msg)
            if value.primary:
                msg = "Nested location bindings cannot override primary ownership"
                raise ValueError(msg)
            validate_bindings(dict(value.locations), "locations")
            validate_bindings(value.parameters, "parameters")
        else:
            check_json_value(value, path)


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _Bindings:
    locations: dict[str, Input | LocationDependency] = field(
        default_factory=dict, init=False, kw_only=True
    )
    parameters: dict[str, object] = field(default_factory=dict, kw_only=True)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, bindings: dict[str, object]) -> dict[str, object]:
        """Return independently captured whole-value parameter arguments."""
        validate_bindings(bindings, "parameters")
        return deepcopy(bindings)

    @field_serializer("parameters")
    @staticmethod
    def describe_parameters(bindings: dict[str, object]) -> dict[str, object]:
        """Return inspection arguments that distinguish references from literals."""
        return {
            name: value
            if isinstance(value, Input)
            else {"kind": "literal", "value": value}
            for name, value in bindings.items()
        }

    def bind(self, **locations: object) -> Self:
        """Return independent bindings with named location overrides.

        References at every nesting level belong to the declaring caller.

        Raises:
            ValueError: If a provider requirement is given invocation bindings.
        """
        if isinstance(self, LocationDependency) and self.provider is not None:
            msg = "Provider requirements do not accept invocation bindings"
            raise ValueError(msg)
        validate_bindings(locations, "locations")
        result = deepcopy(self)
        result.locations.update(
            deepcopy(cast("dict[str, Input | LocationDependency]", locations))
        )
        validate_bindings(dict(result.locations), "locations")
        validate_bindings(result.parameters, "parameters")
        return result


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class LocationDependency(_Bindings):
    """Require provider records or invoke an operation with caller bindings."""

    operation: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    provider: str | None = Field(default=None, min_length=1, strict=True, kw_only=True)
    primary: bool = Field(default=False, strict=True, kw_only=True)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        """Require one source and keep invocation arguments on operations.

        Returns:
            The validated declaration.

        Raises:
            ValueError: If sources or invocation arguments conflict.
        """
        if (self.provider is None) == (self.operation is None):
            msg = "LocationDependency requires exactly one of provider or operation"
            raise ValueError(msg)
        if self.provider is not None and (self.parameters or self.locations):
            msg = "Provider requirements do not accept invocation bindings"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Dependency(_Bindings):
    """Select a named metric output using caller-owned input bindings."""

    producer: str = Field(pattern=r"^[a-z][a-z0-9_]*$", kw_only=True)
    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


def describe_dependency(dependency: Dependency | LocationDependency) -> dict:
    """Return a detached serializable inspection view of a live declaration."""
    return TypeAdapter(type(dependency)).dump_python(dependency, mode="json")
