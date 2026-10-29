"""Serialize and validate resolved execution inputs independently of provenance."""

from collections.abc import Mapping

from lyra.sdk.arguments import PreparedResolvedInvocation, serialize_resolved_input
from lyra.sdk.errors import MetricInputError
from lyra.sdk.models.location import LocationSelection
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.types import validate_json_object
from pydantic import ValidationError


def prepare_resolved_job_input(
    definition: PluginDefinition,
    metric: str,
    payload: Mapping[str, object],
    caller_input: Mapping[str, object],
) -> PreparedResolvedInvocation:
    """Validate queued scope against caller selections without acquiring resources.

    Returns:
        The same effective invocation used by local execution.

    Raises:
        MetricInputError: If queued inputs or their selection scope are inconsistent.
    """
    try:
        inputs = validate_json_object(dict(payload))
    except ValidationError as exc:
        raise MetricInputError(metric, "input", str(exc)) from exc
    if set(caller_input) != set(inputs):
        raise MetricInputError(metric, "input", "caller input fields differ")
    preparation_input: dict[str, object] = dict(inputs)
    if "parameters" in inputs:
        # Author validators operate on caller values, not their own output.
        preparation_input["parameters"] = caller_input["parameters"]
    invocation = definition.prepare_resolved_invocation(metric, preparation_input)
    if "parameters" in inputs and (
        serialize_resolved_input(invocation)["parameters"] != inputs["parameters"]
    ):
        raise MetricInputError(metric, "parameters", "effective parameters differ")
    for name, resolved in invocation.locations.items():
        try:
            selection = LocationSelection.model_validate(caller_input[name])
        except ValidationError as exc:
            raise MetricInputError(metric, name, str(exc)) from exc
        ids = [feature.id for feature in resolved.location.features]
        if (
            selection.collection != resolved.collection
            or selection.unit_type != resolved.unit_type
            or set(selection.ids) != set(ids)
            or len(ids) != len(set(ids))
        ):
            raise MetricInputError(
                metric, name, "resolved selection scope or IDs differ"
            )
    return invocation
