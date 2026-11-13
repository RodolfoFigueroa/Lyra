"""Shared provider and invocation fixtures for selector behavior tests."""

from collections.abc import Callable

from lyra.contracts.dependencies import LocationDependency
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.provider_registry import RegisteredProvider, capture_provider
from lyra.sdk.managed_provider import ManagedLocationProvider
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.location_operations import Places
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration, frame


class ManagedProvider(FakeProvider, ManagedLocationProvider):
    id = "managed"
    schema = declaration()


def plugin(
    handler: Callable, selector: Callable, target: str = "managed"
) -> PluginDefinition:
    location_operation(
        name="select_targets",
        code_revision="1",
        description="Select target records",
        inputs={"declared_source": LocationDependency(provider=Places.id)},
        target_provider=target,
        target_selector=selector,
    )(handler)
    return PluginDefinition(
        location_operations=[handler],
        resources={"places": Places, "managed": ManagedProvider},
    )


def prepared(
    mapping: Callable,
    selector: Callable,
    values: dict | None = None,
) -> tuple[
    BoundPlugin, PreparedResolvedInvocation, RegisteredProvider, NativeLocationInput
]:
    binding = BoundPlugin(plugin(mapping, selector))
    target = capture_provider("managed", ManagedProvider)
    source = NativeLocationInput(
        frame(), capture_provider("places", Places).contract.reference
    )
    inputs: dict[str, object] = {"declared_source": source}
    if values is not None:
        inputs["parameters"] = values
    invocation = binding.prepare_resolved_invocation("select_targets", inputs)
    candidates = NativeLocationInput(frame(), target.contract.reference)
    return binding, invocation, target, candidates
