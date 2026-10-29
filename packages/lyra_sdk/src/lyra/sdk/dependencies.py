"""Local dependency declarations with explicit input references."""

import re
from dataclasses import field
from typing import Literal

from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.schema import check_json_value
from lyra.sdk.types import JsonValue
from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Input:
    """Reference a named location or direct parameters.field on the consumer."""

    path: str = Field(pattern=r"^(parameters\.)?[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["input"] = field(default="input", kw_only=True)


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Dependency:
    """Reference an output and optional location and whole-field parameter overrides."""

    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, dict[str, object]] = field(default_factory=dict, kw_only=True)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(
        cls, arguments: dict[str, dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        """Check locally knowable override syntax.

        Returns:
            The validated dependency arguments.

        Raises:
            ValueError: If overrides use unsupported fields or values.
        """
        if set(arguments) - {"parameters", "locations"}:
            msg = "Dependency arguments support only parameters and locations."
            raise ValueError(msg)
        for category, bindings in arguments.items():
            for name, value in bindings.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    msg = "Overrides must target direct input fields."
                    raise ValueError(msg)
                if category == "locations" and not isinstance(value, Input):
                    msg = "Location bindings require Input references."
                    raise ValueError(msg)
                if not isinstance(value, Input):
                    check_json_value(value, f"parameters.{name}")
        return arguments


class LiteralArgument(StrictBaseModel):
    """A complete JSON literal, distinct from a parameter reference."""

    kind: Literal["literal"] = "literal"
    value: JsonValue


class DependencyManifest(StrictBaseModel):
    """Persisted dependency metadata with unambiguous argument bindings."""

    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[
        Literal["parameters", "locations"], dict[str, Input | LiteralArgument]
    ] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(
        cls,
        arguments: dict[
            Literal["parameters", "locations"], dict[str, Input | LiteralArgument]
        ],
    ) -> dict[Literal["parameters", "locations"], dict[str, Input | LiteralArgument]]:
        """Return persisted bindings validated using the declaration syntax rules."""
        Dependency.validate_arguments(
            {
                category: {
                    name: value if isinstance(value, Input) else value.value
                    for name, value in bindings.items()
                }
                for category, bindings in arguments.items()
            }
        )
        return arguments

    @classmethod
    def from_declaration(cls, dependency: Dependency) -> "DependencyManifest":
        """Return serializable metadata without interpreting literal objects."""
        arguments: dict[
            Literal["parameters", "locations"], dict[str, Input | LiteralArgument]
        ] = {}
        for category in ("parameters", "locations"):
            if category in dependency.arguments:
                arguments[category] = {
                    name: value
                    if isinstance(value, Input)
                    else LiteralArgument.model_validate({"value": value})
                    for name, value in dependency.arguments[category].items()
                }
        return cls(output=dependency.output, arguments=arguments)
