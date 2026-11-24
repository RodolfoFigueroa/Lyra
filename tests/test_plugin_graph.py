"""SDK dependency validation and startup integration."""

from pathlib import Path

import pandas as pd
import pytest

from lyra.contracts.config import InstalledPluginConfig
from lyra.contracts.dependencies import Dependency, Input
from lyra.contracts.plugins import MetricManifest
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import registry
from test_support.plugin_graph import (
    Defaulted,
    Nested,
    Renamed,
    Required,
    manifest,
    node,
)
from test_support.table_fixtures import value_schema
from tests.config_helpers import load_test_config
from tests.graph_arguments_helpers import parameter_edge
from tests.live_graph_helpers import plugins
from tests.plugin_helpers import DEFINITIONS, PATHS, VERSIONS


def test_converging_graph_and_order_independence() -> None:
    definitions = plugins()
    first = compile_live_catalog(definitions)
    second = compile_live_catalog(dict(reversed(definitions.items())))
    for graph in (first.graph, second.graph):
        deps = graph.dependencies["consumer"]
        assert (
            deps["first"].output.producer.node.name
            == deps["second"].output.producer.node.name
            == "producer"
        )
        assert deps["first"].output.declaration == deps["second"].output.declaration
        assert graph.order.index("producer") < graph.order.index("consumer")


@pytest.mark.parametrize(
    ("caller", "target", "overrides"),
    [
        (Renamed, Defaulted, {}),
        (Required, Required, {}),
        (Renamed, Required, {"year": Input("parameters.vintage")}),
        (Renamed, Required, {"year": "validate at invocation"}),
    ],
)
def test_parameter_supply_paths(
    caller: type[MetricParameters], target: type[MetricParameters], overrides: dict
) -> None:
    edge = parameter_edge(
        "consumer",
        caller,
        Dependency("producer", producer="producer", parameters=overrides),
        target,
    )
    assert edge.dependency.parameters == overrides


@pytest.mark.parametrize(
    ("caller", "overrides", "reason"),
    [
        (Renamed, {}, "No parameter supply"),
        (Nested, {}, "No parameter supply"),
        (Renamed, {"unknown": 1}, "Unknown receiving parameter"),
        (Renamed, {"year": Input("parameters.missing")}, "undeclared input reference"),
    ],
)
def test_invalid_parameter_supply(
    caller: type[MetricParameters], overrides: dict, reason: str
) -> None:
    with pytest.raises((ValueError, TypeError), match=reason):
        parameter_edge(
            "consumer",
            caller,
            Dependency("producer", producer="producer", parameters=overrides),
            Required,
        )


def test_catalog_graph_visibility_and_enabled_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = value_schema()

    @metric(
        name="source",
        description="Source",
        private=True,
        code_revision="1",
        outputs={"value": schema},
    )
    def source() -> pd.DataFrame:
        pytest.fail("Catalog compilation invoked source")

    @metric(
        name="public",
        description="Public consumer",
        code_revision="1",
        inputs={"data": Dependency("value", producer="source")},
        outputs={"first": schema, "second": schema},
    )
    def public(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
        return {"first": data, "second": data}

    config = load_test_config(tmp_path, plugins=[])
    for name, handler in (("producer", source), ("consumer", public)):
        monkeypatch.setitem(PATHS, name, tmp_path)
        monkeypatch.setitem(VERSIONS, name, "1")
        monkeypatch.setitem(DEFINITIONS, name, PluginDefinition(metrics=[handler]))
        config.plugins.installed.append(InstalledPluginConfig(distribution=name))
    catalog = registry.Catalog.load(config.plugins)
    assert catalog.get_loaded_metric_names() == ["public"]
    assert "source" not in catalog.get_loaded_metric_names()
    with pytest.raises(KeyError):
        catalog.discovery.inspect("source")
    assert [item.name for item in catalog.discovery.search()] == ["public"]
    assert set(catalog.compiled.graph.nodes) == {"public", "source"}
    info = catalog.discovery.inspect("public")
    assert info is not None
    assert set(info.outputs) == {"first", "second"}
    config.plugins.installed[0].enabled = False
    with pytest.raises(ValueError, match="source"):
        registry.Catalog.load(config.plugins)


@pytest.mark.parametrize("case", ["duplicate", "collision"])
def test_local_manifest_rejects_output_conflicts(case: str) -> None:
    source = node("source", outputs=("table",))
    other = (
        node("other", outputs=("table",))
        if case == "duplicate"
        else node("table", outputs=("different",))
    )
    assert manifest("plugin", source, other)


def test_parameter_contract_rejects_non_object_root() -> None:
    source = node("source", parameters=Defaulted)
    payload = source.model_dump()
    payload["parameters_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"Value": {"type": "integer"}},
        "$ref": "#/$defs/Value",
    }
    with pytest.raises(ValueError, match="object"):
        MetricManifest.model_validate(payload)


@pytest.mark.parametrize(
    "literal",
    [
        None,
        ["lot-id"],
        {"type": "FeatureCollection"},
        {"kind": "input", "path": "lots"},
    ],
)
def test_location_literals_rejected_in_declarations(
    literal: object,
) -> None:
    with pytest.raises(TypeError, match=r"locations.location.*require Input"):
        Dependency("source", producer="source").bind(location=literal)


def test_reference_looking_parameter_is_literal() -> None:
    literal = {"kind": "input", "path": "lots"}
    dependency = Dependency(
        "source", producer="source", parameters={"settings": literal}
    ).bind(location=Input("lots"))
    literal["path"] = "changed"
    assert dependency.locations["location"] == Input("lots")
    assert dependency.parameters["settings"] == {"kind": "input", "path": "lots"}


@pytest.mark.parametrize("explicit", [False, True])
def test_incompatible_provider_requires_a_route(*, explicit: bool) -> None:
    dependency = Dependency("value", producer="producer")
    if explicit:
        dependency = dependency.bind(places=Input("seed"))
    with pytest.raises(
        (ValueError, TypeError), match=r"No canonical route|No location supply"
    ):
        compile_live_catalog(plugins(dependency))
