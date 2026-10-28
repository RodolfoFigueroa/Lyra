"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lyra.sdk.dependencies import Input
from lyra.sdk.errors import MetricInputError
from lyra.sdk.graph import parameter_contract
from pydantic import BaseModel

if TYPE_CHECKING:
    from lyra.sdk.graph import ResolvedDependency
    from lyra.sdk.models.geometry import GeoJSON, SingleGeoJSON
    from lyra.sdk.plugin import PluginDefinition


def _parameter_values(model: BaseModel) -> dict[str, object]:
    """Read effective fields independently of serialization exclusion options.

    Returns:
        All declared fields with nested models converted to JSON containers.
    """
    return {
        name: _parameter_value(getattr(model, name))
        for name in type(model).model_fields
    }


def _parameter_value(value: object) -> object:
    """Normalize the SDK's supported nested models and JSON containers.

    Returns:
        The scalar value or recursively normalized container.
    """
    if isinstance(value, BaseModel):
        return _parameter_values(value)
    if isinstance(value, dict):
        return {key: _parameter_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_parameter_value(item) for item in value]
    return value


@dataclass(frozen=True)
class PreparedInvocation:
    """Validated ordinary inputs, excluding context and dependency tables.

    Treat the contained models as read-only throughout graph execution.
    """

    node: str
    parameters: BaseModel | None
    spatial: dict[str, GeoJSON | SingleGeoJSON]

    @property
    def identity(self) -> str:
        """Canonical serialized inputs for reuse within one job.

        Returns:
            A stable key preserving list order and literal geometry encoding.
        """
        return json.dumps(
            {
                "node": self.node,
                "parameters": _parameter_values(self.parameters)
                if self.parameters is not None
                else None,
                "spatial": {
                    name: value.model_dump(mode="json")
                    for name, value in self.spatial.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def arguments(self) -> dict[str, object]:
        """Return handler inputs before context and dependency injection."""
        result: dict[str, object] = dict(self.spatial)
        if self.parameters is not None:
            result["parameters"] = self.parameters
        return result


def prepare_dependency(
    consumer: PreparedInvocation,
    edge: ResolvedDependency,
    plugin: PluginDefinition,
) -> PreparedInvocation:
    """Forward a resolved edge and validate using the producer implementation.

    The caller supplies the plugin owning the producer. Spatial values must
    already be resolved; no acquisition or handler execution occurs here.

    Returns:
        The producer's effective inputs, ready for dependency resolution or reuse.

    Raises:
        MetricInputError: If forwarding or producer validation fails. The cause
            retains the original validation error and its field path.
    """
    try:
        arguments = _dependency_arguments(consumer, edge)
        return plugin.prepare_invocation(edge.output.producer.node.name, arguments)
    except MetricInputError as exc:
        raise MetricInputError(
            edge.output.producer.node.name,
            exc.path,
            f"{edge.diagnostic}: {exc.message}",
        ) from exc


def _dependency_arguments(
    consumer: PreparedInvocation, edge: ResolvedDependency
) -> dict[str, object]:
    producer = edge.output.producer.node
    if consumer.node != edge.consumer.node.name:
        raise MetricInputError(producer.name, "input", "consumer node mismatch")
    source = (
        _parameter_values(consumer.parameters)
        if consumer.parameters is not None
        else {}
    )
    values = {
        name: source[name]
        for name in parameter_contract(producer).get("properties", {})
        if name in source
    }
    for name, binding in edge.dependency.arguments.get("parameters", {}).items():
        if isinstance(binding, Input):
            field = binding.path.split(".")[1]
            if field not in source:
                raise MetricInputError(
                    producer.name, binding.path, "missing referenced input"
                )
            values[name] = source[field]
        else:
            values[name] = binding.value
    arguments: dict[str, object] = {}
    properties = producer.request_schema.get("properties", {})
    if isinstance(properties, dict) and "parameters" in properties:
        arguments["parameters"] = values
    for name in producer.spatial_inputs:
        if name not in consumer.spatial:
            raise MetricInputError(producer.name, name, "missing spatial input")
        arguments[name] = consumer.spatial[name]
    return arguments
