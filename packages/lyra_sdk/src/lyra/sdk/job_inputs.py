"""Prepare public selections for local and queued execution."""

from collections.abc import Mapping

from lyra.sdk.arguments import PreparedSelectionInvocation
from lyra.sdk.errors import LocationSelectionError, MetricInputError
from lyra.sdk.models.location import LocationSelection
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.types import validate_json_object
from pydantic import ValidationError


def prepare_selection_input(
    definition: PluginDefinition, metric: str, inputs: Mapping[str, object]
) -> PreparedSelectionInvocation:
    """Validate public fields and prepare author parameters without acquisition.

    Returns:
        Selections and effective parameters ready for execution.

    Raises:
        MetricInputError: If input fields or parameters are invalid.
        LocationSelectionError: If a selection is malformed.
    """
    if metric not in definition.metric_names:
        raise MetricInputError(metric, "metric", "Unknown public metric")
    manifest = next(node for node in definition.node_manifests if node.name == metric)
    expected = set(manifest.spatial_inputs)
    properties = manifest.request_schema["properties"]
    if isinstance(properties, dict) and "parameters" in properties:
        expected.add("parameters")
    if set(inputs) != expected:
        raise MetricInputError(metric, "input", f"expected fields {sorted(expected)}")
    parameters = (
        definition.prepare_parameters(metric, inputs["parameters"])
        if "parameters" in inputs
        else None
    )
    selections = {}
    for name in manifest.spatial_inputs:
        try:
            selections[name] = LocationSelection.model_validate(inputs[name])
        except ValidationError as exc:
            raise LocationSelectionError(
                name, "Invalid location selection", []
            ) from exc
    return PreparedSelectionInvocation(metric, parameters, selections)


def prepare_job_input(
    definition: PluginDefinition,
    metric: str,
    payload: Mapping[str, object],
    caller_input: Mapping[str, object],
) -> PreparedSelectionInvocation:
    """Check queued selections against provenance and prepare execution.

    Returns:
        The same prepared selections used by local execution.

    Raises:
        MetricInputError: If queue and provenance inputs differ or are not JSON.
    """
    try:
        inputs = validate_json_object(dict(payload))
    except ValidationError as exc:
        raise MetricInputError(metric, "input", "Invalid queued JSON input") from exc
    if inputs != caller_input:
        raise MetricInputError(metric, "input", "Queued input differs from provenance")
    return prepare_selection_input(definition, metric, inputs)
