"""Authoring invocation declarations and manifest conversions."""

from __future__ import annotations

from dataclasses import field

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass

from lyra.contracts import dependencies as contracts


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _Bindings:
    locations: dict[str, object] = field(default_factory=dict, kw_only=True)
    parameters: dict[str, object] = field(default_factory=dict, kw_only=True)

    @field_validator("locations")
    @classmethod
    def validate_locations(cls, bindings: dict[str, object]) -> dict[str, object]:
        """Return locally validated location bindings."""
        contracts.validate_bindings(bindings, "locations", LocationDependency)
        return bindings

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, bindings: dict[str, object]) -> dict[str, object]:
        """Return locally validated whole-field parameter bindings."""
        contracts.validate_bindings(bindings, "parameters", LocationDependency)
        return bindings


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class LocationDependency(_Bindings):
    """Invoke an operation; nested references belong to the declaration's caller.

    Explicit bindings override matching-name forwarding from that caller.
    """

    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Dependency(_Bindings):
    """Invoke a concrete producer/output with overrides or matching-name forwarding."""

    producer: str = Field(pattern=r"^[a-z][a-z0-9_]*$", kw_only=True)
    output: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


def _manifest_bindings(dependency: _Bindings) -> dict[str, object]:
    return {
        "locations": {
            name: location_dependency_manifest(value)
            if isinstance(value, LocationDependency)
            else value
            for name, value in dependency.locations.items()
        },
        "parameters": {
            name: value
            if isinstance(value, contracts.Input)
            else contracts.LiteralArgument.model_validate({"value": value})
            for name, value in dependency.parameters.items()
        },
    }


def location_dependency_manifest(
    dependency: LocationDependency,
) -> contracts.LocationDependencyManifest:
    """Return a serialized authoring invocation and its nested bindings."""
    return contracts.LocationDependencyManifest.model_validate(
        {"operation": dependency.operation, **_manifest_bindings(dependency)}
    )


def location_dependency_declaration(
    manifest: contracts.LocationDependencyManifest,
) -> LocationDependency:
    """Return an authoring invocation reconstructed from manifest bindings."""
    return LocationDependency(
        manifest.operation,
        locations={
            name: value
            if isinstance(value, contracts.Input)
            else location_dependency_declaration(value)
            for name, value in manifest.locations.items()
        },
        parameters={
            name: value if isinstance(value, contracts.Input) else value.value
            for name, value in manifest.parameters.items()
        },
    )


def dependency_manifest(dependency: Dependency) -> contracts.DependencyManifest:
    """Return a serialized producer invocation preserving literal objects."""
    return contracts.DependencyManifest.model_validate(
        {
            "producer": dependency.producer,
            "output": dependency.output,
            **_manifest_bindings(dependency),
        }
    )
