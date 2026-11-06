"""Bind registered plugin handlers for resolved-input validation and execution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import geopandas as gpd
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from lyra.contracts.errors import MetricInputError, PluginDefinitionError
from lyra.contracts.schema import check_json_value, subschema_validator
from lyra.data.locations import validated_locations
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.planning.computation import executable_contract
from lyra.runtime.resources import resource_invocation
from lyra.sdk.results import validate_registered_result

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pandera.pandas as pa
    from pydantic import BaseModel

    from lyra.contracts.locations import LocationFamily
    from lyra.contracts.plugins import NodeManifest
    from lyra.contracts.providers import LocationProviderManifest
    from lyra.data.locations import Locations
    from lyra.data.results import NativeOutputs
    from lyra.sdk.context import RunContext
    from lyra.sdk.plugin import NodeImplementation, PluginDefinition


class BoundPlugin:
    """A plugin's registered implementations ready for execution."""

    def __init__(self, definition: PluginDefinition) -> None:
        """Capture owned descriptors and resource registrations without acquisition."""
        self.definition = definition
        self.resources = definition.resources
        self._nodes = {
            name: definition.implementation(name) for name in definition.node_names
        }

    def _get(self, name: str) -> NodeImplementation:
        try:
            return self._nodes[name]
        except KeyError as exc:
            available = ", ".join(self._nodes)
            msg = f"Metric {name!r}: unknown node; available nodes: {available}"
            raise PluginDefinitionError(msg) from exc

    def node_manifest(self, name: str) -> NodeManifest | None:
        """Return a copy of the bound node contract for accepted-plan verification."""
        node = self._nodes.get(name)
        return node.manifest.model_copy(deep=True) if node is not None else None

    def matches_location_provider(self, declaration: LocationProviderManifest) -> bool:
        """Return whether this binding implements the selected provider contract."""
        provider = self.definition.location_provider
        return provider is not None and executable_contract(
            provider
        ) == executable_contract(declaration)

    def prepare_parameters(self, name: str, value: object) -> BaseModel:
        """Validate parameter JSON before parsing it through the author's model.

        Returns:
            The typed model, including validated defaults and semantic checks.

        Raises:
            PluginDefinitionError: If the metric declares no parameter model.
            MetricInputError: If the input is not valid JSON or fails validation.
        """
        definition = self._get(name)
        model = definition.parameters
        if model is None:
            msg = f"Metric {name!r}: metric has no parameters"
            raise PluginDefinitionError(msg)
        schema = definition.manifest.request_schema
        properties = schema["properties"]
        if not isinstance(properties, dict) or not isinstance(
            properties["parameters"], dict
        ):
            msg = f"Metric {name!r}: parameters schema is missing"
            raise PluginDefinitionError(msg)
        try:
            check_json_value(value, "parameters")
            encoded = json.dumps(value, allow_nan=False)
            subschema_validator(schema, properties["parameters"]).validate(value)
            return model.model_validate_json(encoded)
        except SchemaValidationError as exc:
            path = ".".join(["parameters", *(str(item) for item in exc.path)])
            raise MetricInputError(name, path, exc.message) from exc
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            path = ".".join(["parameters", *(str(item) for item in error["loc"])])
            raise MetricInputError(name, path, error["msg"]) from exc
        except (TypeError, ValueError) as exc:
            raise MetricInputError(name, "parameters", str(exc)) from exc

    def output_schemas(self, name: str) -> dict[str, pa.DataFrameSchema]:
        """Return the registered executable output contracts."""
        return dict(self._get(name).schemas)

    def normalize_result(
        self, name: str, value: object, *, lazy: bool = False
    ) -> NativeOutputs:
        """Check a node result and validate its named native tables.

        Returns:
            Checked outputs for the requested metric.

        Raises:
            PluginDefinitionError: If the node is not a metric.
        """
        definition = self._get(name)
        if definition.manifest.kind != "metric":
            msg = f"Node {name!r} is not a metric"
            raise PluginDefinitionError(msg)
        return validate_registered_result(name, definition.schemas, value, lazy=lazy)

    def normalize_location_result(
        self,
        invocation: PreparedResolvedInvocation,
        value: object,
        *,
        lazy: bool = False,
    ) -> Locations:
        """Validate one operation result and return schema-coerced locations.

        Returns:
            Validated locations with declared coercion applied.

        Raises:
            PluginDefinitionError: If the name is not an operation.
            ValueError: If the operation result violates its contract.
            TypeError: If the result is not GeoDataFrame.
        """
        name = invocation.node
        contract = self._get(name).location_schema
        if contract is None:
            msg = f"Node {name!r} is not a location operation"
            raise PluginDefinitionError(msg)
        if not isinstance(value, gpd.GeoDataFrame):
            msg = f"Location operation {name!r}: result must be one GeoDataFrame value"
            raise TypeError(msg)
        node = self._get(name).manifest
        declared = node.output_contract if node.kind == "location_operation" else None
        if declared is None:
            message = "Operation requires a concrete output contract"
            raise PluginDefinitionError(message)
        try:
            if node.kind == "location_operation" and node.canonical:
                # Validate every returned row before reducing overlapping targets.
                validate_registered_result(
                    name, {name: contract.schema}, value, lazy=lazy
                )
                value = (
                    value.drop_duplicates(subset=[contract.id_column])
                    .sort_values(contract.id_column, kind="stable")
                    .reset_index(drop=True)
                )
            return contract.build_contract(value, contract=declared, lazy=lazy)
        except Exception as exc:
            msg = f"Location operation {name!r}: invalid result: {exc}"
            raise ValueError(msg) from exc

    def validate_cached_locations(
        self,
        invocation: PreparedResolvedInvocation,
        value: Locations,
    ) -> Locations:
        """Revalidate a restored snapshot against its operation schema.

        Returns:
            Schema-validated locations, including declared coercion.

        Raises:
            ValueError: If the cached scope differs from the declared scope.
            PluginDefinitionError: If the node is not a location operation.
        """
        name = invocation.node
        scope = self._location_scope(invocation)
        contract = self._get(name).location_schema
        if contract is None:
            msg = f"Node {name!r} is not a location operation"
            raise PluginDefinitionError(msg)
        if (value.collection, value.unit_type) != (scope.collection, scope.unit_type):
            msg = "Cached location scope differs from the operation contract"
            raise ValueError(msg)
        node = self._get(name).manifest
        declared = node.output_contract if node.kind == "location_operation" else None
        if declared is None:
            message = "Operation requires a concrete output contract"
            raise PluginDefinitionError(message)
        if value.contract != declared.reference:
            message = "Cached location differs from its concrete output contract"
            raise ValueError(message)
        return contract.validate(value)

    def _location_scope(self, invocation: PreparedResolvedInvocation) -> LocationFamily:
        """Return the authoritative operation output family.

        Raises:
            PluginDefinitionError: If the node is not a location operation.
        """
        node = self._get(invocation.node).manifest
        if node.kind != "location_operation":
            message = f"Node {invocation.node!r} is not a location operation"
            raise PluginDefinitionError(message)
        return node.output_contract.family

    def invoke_operation(
        self,
        invocation: PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> Locations:
        """Invoke a declared operation with supplied dependencies and context.

        Returns:
            The validated, schema-coerced location result.

        Raises:
            PluginDefinitionError: If the node is not a location operation.
        """
        if self._get(invocation.node).manifest.kind != "location_operation":
            msg = f"Node {invocation.node!r} is not a location operation"
            raise PluginDefinitionError(msg)
        return self.normalize_location_result(
            invocation,
            self._invoke_handler(invocation, dependencies, context_factory),
        )

    def invoke(
        self,
        invocation: PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs:
        """Invoke one prepared node with resolved tables and check its outputs.

        Returns:
            All validated native outputs.

        Raises:
            PluginDefinitionError: If inputs or the runtime context violate the
                node contract.
        """
        if self._get(invocation.node).manifest.kind != "metric":
            msg = f"Node {invocation.node!r} is not a metric"
            raise PluginDefinitionError(msg)
        return self.normalize_result(
            invocation.node,
            self._invoke_handler(invocation, dependencies, context_factory),
        )

    def _invoke_handler(
        self,
        invocation: PreparedResolvedInvocation,
        dependencies: Mapping[str, object],
        context_factory: Callable[[str], RunContext],
    ) -> object:
        definition = self._get(invocation.node)
        if dependencies.keys() != definition.manifest.inputs.keys():
            msg = f"Metric {invocation.node!r}: dependency inputs do not match"
            raise PluginDefinitionError(msg)
        arguments = invocation.arguments() | dict(dependencies)
        if definition.accepts_context or definition.manifest.resources:
            context = context_factory(invocation.node)
            if definition.requires_resolver and not callable(
                getattr(context, "resolve_locations", None)
            ):
                msg = (
                    f"Location operation {invocation.node!r}: "
                    "context requires resolve_locations"
                )
                raise PluginDefinitionError(msg)
            if definition.accepts_context:
                arguments["context"] = context
            if definition.manifest.resources:
                with resource_invocation(context) as scope:
                    arguments.update(
                        {
                            key: scope.get(name)
                            for key, name in definition.manifest.resources.items()
                        }
                    )
                    return definition.function(**arguments)
        return definition.function(**arguments)

    def _validate_location_contract(
        self, name: str, field: str, value: Locations
    ) -> None:
        declared = self._get(name).manifest.location_inputs[field].contract
        if value.contract != declared.reference:
            message = f"Location input {field!r} differs from its concrete contract"
            raise ValueError(message)

    def prepare_resolved_invocation(
        self, name: str, inputs: Mapping[str, object]
    ) -> PreparedResolvedInvocation:
        """Validate effective parameters and scoped locations without acquisition.

        Returns:
            Inputs ready for graph execution, retaining full resolved scope.

        Raises:
            MetricInputError: If fields or scoped location values are invalid.
        """
        definition = self._get(name)
        expected = set(definition.manifest.location_inputs)
        if definition.parameters is not None:
            expected.add("parameters")
        if set(inputs) != expected:
            raise MetricInputError(name, "input", f"expected fields {sorted(expected)}")
        parameters = (
            self.prepare_parameters(name, inputs["parameters"])
            if definition.parameters is not None
            else None
        )
        locations: dict[str, Locations] = {}
        for field in definition.manifest.location_inputs:
            try:
                value = inputs[field]
                locations[field] = validated_locations(value)
                self._validate_location_contract(name, field, locations[field])
            except (
                ValidationError,
                PydanticSerializationError,
                TypeError,
                ValueError,
            ) as exc:
                raise MetricInputError(name, field, str(exc)) from exc
        return PreparedResolvedInvocation(
            name, parameters, locations, kind=definition.manifest.kind
        )
