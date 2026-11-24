"""Bind registered plugin handlers for resolved-input validation and execution."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING

import pandas as pd
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from lyra.contracts.errors import (
    MetricInputError,
    MetricResultError,
    PluginDefinitionError,
)
from lyra.contracts.operations import OPERATION_MAPPING_ADAPTER
from lyra.contracts.value_identity import check_json_value
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.native_inputs import NativeLocationInput, copy_frame
from lyra.runtime.resources import resource_invocation
from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk.results import validate_registered_result
from lyra.sdk.storage_schema import StorageDescription, empty_frame

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import ibis.expr.types as ir
    import pandera.pandas as pa
    from pydantic import BaseModel

    from lyra.contracts.plugins import NodeManifest
    from lyra.data.results import NativeOutputs
    from lyra.runtime.planning.provider_registry import RegisteredProvider
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
        try:
            check_json_value(value, "parameters")
            encoded = json.dumps(value, allow_nan=False)
            return model.model_validate_json(encoded)
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            path = ".".join(["parameters", *(str(item) for item in error["loc"])])
            raise MetricInputError(name, path, error["msg"]) from exc
        except (TypeError, ValueError) as exc:
            raise MetricInputError(name, "parameters", str(exc)) from exc

    def output_schemas(self, name: str) -> dict[str, pa.DataFrameSchema]:
        """Return the registered executable output contracts."""
        return dict(self._get(name).schemas)

    def output_storage(self, name: str) -> Mapping[str, StorageDescription]:
        """Return compiled storage layouts for trusted execution writes."""
        return self._get(name).storage

    def validate_retained_outputs(
        self, name: str, outputs: NativeOutputs
    ) -> NativeOutputs:
        """Validate cached logical values with canonical reconstruction dtypes.

        Returns:
            Detached tables satisfying current executable checks.

        Raises:
            MetricResultError: If retained names differ from the declaration.
        """
        definition = self._get(name)
        if outputs.keys() != definition.schemas.keys():
            raise MetricResultError(name, "outputs", "Retained output names differ")
        schemas = deepcopy(dict(definition.schemas))
        for output, schema in schemas.items():
            layout = definition.storage[output]
            for column, dtype in zip(layout.columns, layout.pandas_dtypes, strict=True):
                if dtype != "geometry":
                    schema.columns[column].dtype = dtype
        value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
        return validate_registered_result(name, schemas, value)

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

    def invoke_target_selector(
        self,
        invocation: PreparedResolvedInvocation,
        target: RegisteredProvider,
    ) -> ir.Table | None:
        """Construct and validate a filter using isolated callback inputs.

        Returns:
            The lazy filter, or None when there are no source rows.

        Raises:
            PluginDefinitionError: If no selector or matching query target exists.
        """
        definition = self._get(invocation.node)
        node = definition.manifest
        if (
            node.kind != "location_operation"
            or definition.target_selector is None
            or target.query is None
            or target.contract != node.output_contract
        ):
            message = "Target selector requires its registered query-capable target"
            raise PluginDefinitionError(message)
        source_name = next(iter(node.location_inputs))
        source = invocation.locations[source_name]
        self._validate_location_contract(invocation.node, source_name, source)
        if not source.ids:
            return None
        relation = target.query.target_relation()
        arguments = invocation.arguments()
        frame = arguments.pop(source_name)
        result = definition.target_selector(frame, relation, **arguments)
        return validate_selector_expression(result, relation)

    def invoke_operation(
        self,
        invocation: PreparedResolvedInvocation,
        candidates: NativeLocationInput | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """Invoke a positional mapping callback and retain completed source groups.

        Returns:
            Detached mappings covering every original distinct source ID.

        Raises:
            PluginDefinitionError: If the node is not an operation.
            ValueError: If mapping types, keys, or target uniqueness are invalid.
        """
        definition = self._get(invocation.node)
        node = definition.manifest
        if node.kind != "location_operation":
            message = f"Node {invocation.node!r} is not a location operation"
            raise PluginDefinitionError(message)
        source_name = next(iter(node.location_inputs))
        source = invocation.locations[source_name]
        self._validate_location_contract(invocation.node, source_name, source)
        owners = dict.fromkeys(source.ids, ())
        if not owners:
            return {}
        if node.has_target_selector:
            if (
                candidates is None
                or candidates.contract.declaration != node.output_contract.reference
            ):
                message = "Mapping requires candidates from its registered target"
                raise ValueError(message)
            positional = (copy_frame(candidates.frame),)
        else:
            if candidates is not None:
                message = "ID-only operations do not accept candidates"
                raise ValueError(message)
            positional = ()
        arguments = invocation.arguments()
        frame = arguments.pop(source_name)
        mapping = OPERATION_MAPPING_ADAPTER.validate_python(
            definition.function(frame, *positional, **arguments)
        )
        if mapping.keys() - owners.keys():
            message = "Operation keys must belong to the original source selection"
            raise ValueError(message)
        if any(len(ids) != len(set(ids)) for ids in mapping.values()):
            message = "Operation target IDs must be unique within each source group"
            raise ValueError(message)
        candidate_ids = set(candidates.ids) if candidates is not None else set()
        if candidates is not None and any(
            target not in candidate_ids
            for targets in mapping.values()
            for target in targets
        ):
            message = "Operation target IDs must belong to the original candidates"
            raise ValueError(message)
        return owners | mapping

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
        if self.has_no_primary_work(invocation):
            return {
                name: empty_frame(schema)
                for name, schema in self._get(invocation.node).schemas.items()
            }
        return self.normalize_invocation_result(
            invocation,
            self._invoke_handler(invocation, dependencies, context_factory),
        )

    def has_no_primary_work(self, invocation: PreparedResolvedInvocation) -> bool:
        """Distinguish absent partitions from needed whole-computation work.

        Returns:
            Whether the metric has no supplied primary partitions.
        """
        node = self._get(invocation.node).manifest
        return (
            node.kind == "metric"
            and node.primary_input is not None
            and not invocation.locations[node.primary_input].ids
        )

    def normalize_invocation_result(
        self, invocation: PreparedResolvedInvocation, value: object
    ) -> NativeOutputs:
        """Validate native results against schemas and captured primary ownership.

        Returns:
            Detached tables eligible for downstream use and retention.

        Raises:
            MetricResultError: If an output owner was not supplied to the primary.
        """
        outputs = self.normalize_result(invocation.node, value)
        node = self._get(invocation.node).manifest
        if node.kind == "metric" and node.primary_input is not None:
            primary = invocation.locations[node.primary_input]
            self._validate_location_contract(
                invocation.node, node.primary_input, primary
            )
            owners = frozenset(primary.ids)
            for name, frame in outputs.items():
                if not frame["location_id"].isin(owners).all():
                    raise MetricResultError(
                        invocation.node,
                        f"output {name!r}",
                        "Output owners must belong to the original primary selection",
                    )
        return outputs

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
        arguments = invocation.arguments() | {
            name: copy_frame(value)
            if isinstance(value, pd.DataFrame)
            else deepcopy(value)
            for name, value in dependencies.items()
        }
        if definition.accepts_context or definition.manifest.resources:
            context = context_factory(invocation.node)
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
        self, name: str, field: str, value: NativeLocationInput
    ) -> None:
        declared = self._get(name).manifest.location_inputs[field].contract
        if value.contract.declaration != declared.reference:
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
        allowed = set(definition.manifest.location_inputs)
        expected = {
            name
            for name, declaration in definition.manifest.location_inputs.items()
            if declaration.dependency is None
            or declaration.dependency.provider is not None
        }
        if definition.parameters is not None:
            expected.add("parameters")
            allowed.add("parameters")
        if not expected <= inputs.keys() or inputs.keys() - allowed:
            raise MetricInputError(name, "input", f"expected fields {sorted(expected)}")
        parameters = (
            self.prepare_parameters(name, inputs["parameters"])
            if definition.parameters is not None
            else None
        )
        locations: dict[str, NativeLocationInput] = {}
        for field in definition.manifest.location_inputs:
            if field not in inputs:
                continue
            value = inputs[field]
            if not isinstance(value, NativeLocationInput):
                raise MetricInputError(
                    name, field, "Expected execution-owned native records"
                )
            try:
                locations[field] = value
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
