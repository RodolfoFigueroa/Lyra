"""Shared catalog capture and complete-catalog validation."""

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.errors import ResourceConfigurationError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.routing_plugins import provider
from test_support.workloads import catalog, installed, workload


def metric_plugin(name: str, upstream: str | None = None) -> PluginDefinition:
    schema = pa.DataFrameSchema(strict=True)

    def source() -> pd.DataFrame:
        return pd.DataFrame()

    def consumer(data: pd.DataFrame) -> pd.DataFrame:
        return data

    inputs = {"data": Dependency(upstream, producer=upstream)} if upstream else {}
    handler = metric(
        name=name,
        code_revision="1",
        description=name,
        outputs={name: schema},
        inputs=inputs,
    )(consumer if upstream else source)
    return PluginDefinition(metrics=[handler])


def test_catalog_captures_declarations() -> None:
    definitions = {"one": metric_plugin("first"), "two": metric_plugin("second")}
    compiled = compile_live_catalog(definitions)
    definitions.clear()
    assert (
        set(compiled.graph.nodes)
        == set(compiled.accepted_sources)
        == {"first", "second"}
    )
    assert len(compiled.manifests["one"].nodes) == 1
    assert not compiled.providers


def test_duplicate_concrete_identity_across_plugins() -> None:
    with pytest.raises(ValueError, match="Duplicate node"):
        compile_live_catalog(
            {"one": metric_plugin("same"), "two": metric_plugin("same")}
        )


def test_conflicting_resources_across_plugins() -> None:
    with pytest.raises(ResourceConfigurationError, match="Conflicting resource"):
        compile_live_catalog(
            {
                key: PluginDefinition(resources={"shared": provider(key)})
                for key in ("first", "second")
            }
        )


def test_cross_plugin_dependencies_and_cycles() -> None:
    definitions = {
        "one": metric_plugin("source"),
        "two": metric_plugin("consumer", "source"),
    }
    compiled = compile_live_catalog(definitions)
    assert compiled.graph.order == ("source", "consumer")
    definitions["one"] = metric_plugin("source", "consumer")
    with pytest.raises(ValueError, match="cycle"):
        compile_live_catalog(definitions)


def test_application_and_local_share_contracts() -> None:
    definition = workload(1, "catalog", 10).plugin
    declarations = installed(definition)
    application = catalog(declarations)
    local = LocalRunner.from_definitions({declarations[0].distribution: definition})
    assert application.compiled.graph == local.catalog.graph
    assert application.compiled.providers == local.catalog.providers


def test_application_registry_uses_captured_contracts() -> None:
    declarations = installed(workload(1, "catalog", 10).plugin)
    application = catalog(declarations)
    name, entry = next(iter(application.compiled.graph.nodes.items()))
    original = next(
        node for node in declarations[0].definition.node_manifests if node.name == name
    )
    expected_schema = entry.node.request_schema.copy()
    original.request_schema.clear()
    assert entry.node.request_schema == expected_schema
    application.compiled.manifests[entry.distribution].nodes[0].request_schema.clear()
    assert entry.node.request_schema == expected_schema
