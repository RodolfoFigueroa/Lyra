"""Shared catalog capture and complete-catalog validation."""

import pytest
from lyra.sdk import LocalRunner
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.dependencies import Dependency
from lyra.sdk.models.plugin import PluginManifest

from benchmarks.workloads import catalog, installed, workload
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.test_plugin_graph import manifest as graph_manifest
from tests.test_plugin_graph import node


def manifest(name: str) -> PluginManifest:
    return PluginManifest.model_validate(plugin_manifest(metric_manifest(name=name)))


def test_catalog_captures_declarations_and_reuses_families() -> None:
    source = manifest("first")
    compiled = compile_catalog({"one": source, "two": manifest("second")})
    source.nodes.clear()
    assert set(compiled.graph.nodes) == {"first", "second"}
    assert len(compiled.manifests["one"].nodes) == 1
    assert compiled.editions is compiled.graph.editions
    assert compiled.location_provider is None


def test_duplicate_concrete_identity_across_plugins() -> None:
    with pytest.raises(ValueError, match="Duplicate node"):
        compile_catalog({"one": manifest("same"), "two": manifest("same")})


def test_conflicting_resources_across_plugins() -> None:
    first, second = manifest("first"), manifest("second")
    first.resources["shared"] = {"kind": "first"}
    second.resources["shared"] = {"kind": "second"}
    with pytest.raises(ValueError, match="Conflicting resource"):
        compile_catalog({"one": first, "two": second})


def test_cross_plugin_dependencies_and_cycles() -> None:
    source = node("source")
    consumer = node(
        "consumer", inputs={"data": Dependency("source", producer="source")}
    )
    manifests = {
        "consumer": graph_manifest("consumer", consumer),
        "source": graph_manifest("source", source),
    }
    compiled = compile_catalog(manifests)
    assert compiled.graph.order.index("source") < compiled.graph.order.index("consumer")
    source.inputs.update(
        node(
            "source", inputs={"data": Dependency("consumer", producer="consumer")}
        ).inputs
    )
    with pytest.raises(ValueError, match=r"[Cc]ycle"):
        compile_catalog(manifests)


def test_family_validation_spans_plugins() -> None:
    first = node("first").model_copy(update={"family": "shared", "edition": "one"})
    second = node("second").model_copy(update={"family": "shared", "edition": "two"})
    with pytest.raises(ValueError, match="shared"):
        compile_catalog(
            {
                "one": graph_manifest("one", first),
                "two": graph_manifest("two", second),
            }
        )


def test_application_and_local_share_contracts() -> None:
    definition = workload(1, "catalog", 10).plugin
    declarations = installed(definition)
    application = catalog(declarations)
    local = LocalRunner.from_definitions({declarations[0].distribution: definition})
    assert application.compiled.graph == local.catalog.graph
    assert application.compiled.location_provider == local.catalog.location_provider


def test_application_registry_uses_captured_contracts() -> None:
    declarations = installed(workload(1, "catalog", 10).plugin)
    application = catalog(declarations)
    name, entry = next(iter(application.compiled.graph.nodes.items()))
    original = next(
        node for node in declarations[0].manifest.nodes if node.name == name
    )
    expected_schema = entry.node.request_schema.copy()
    original.request_schema.clear()
    assert entry.node.request_schema == expected_schema
    assert entry.node is application.compiled.manifests[entry.distribution].nodes[0]
