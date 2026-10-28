"""Local dependency declarations with explicit parameter references."""

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
    """Reference one direct parameter field on the consuming node."""

    path: str = Field(pattern=r"^parameters\.[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["input"] = field(default="input", kw_only=True)


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Dependency:
    """Reference an output and optional whole-field parameter overrides."""

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
        if set(arguments) - {"parameters"}:
            msg = "Dependency arguments support only parameters."
            raise ValueError(msg)
        for name, value in arguments.get("parameters", {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                msg = "Overrides must target direct parameter fields."
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
    arguments: dict[Literal["parameters"], dict[str, Input | LiteralArgument]] = Field(
        default_factory=dict
    )

    @classmethod
    def from_declaration(cls, dependency: Dependency) -> "DependencyManifest":
        """Return serializable metadata without interpreting literal objects."""
        fields = dependency.arguments.get("parameters")
        arguments: dict[Literal["parameters"], dict[str, Input | LiteralArgument]] = {}
        if fields is not None:
            arguments["parameters"] = {
                name: value
                if isinstance(value, Input)
                else LiteralArgument.model_validate({"value": value})
                for name, value in fields.items()
            }
        return cls(output=dependency.output, arguments=arguments)
