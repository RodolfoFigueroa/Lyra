"""Manifest-only graph validation and startup integration."""

import importlib
from pathlib import Path

import pytest
from lyra.sdk import MetricParameters
from lyra.sdk.dependencies import Dependency, DependencyManifest, Input, LiteralArgument
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import NodeManifest, PluginInfo, PluginManifest
from lyra.sdk.parameters import build_request_schema
from pydantic import Field

from lyra_app import registry
from tests.config_helpers import load_test_config
from tests.plugin_helpers import VERSIONS, plugin_config


class Required(MetricParameters):
    year: int | None = Field(description="Required even when nullable.")


class Defaulted(MetricParameters):
    year: int | None = Field(default=None, description="Default year.")


class Renamed(MetricParameters):
    vintage: int = Field(default=2020, description="Consumer year.")


class Nested(MetricParameters):
    settings: Required = Field(description="Nested settings.")


def node(
    name: str,
    *,
    outputs: tuple[str, ...] | None = None,
    inputs: dict[str, Dependency] | None = None,
    parameters: type[MetricParameters] | None = None,
    spatial: set[str] | None = None,
) -> NodeManifest:
    return NodeManifest(
        name=name,
        description=name,
        request_schema=build_request_schema(name, parameters, set(spatial or ())),
        spatial_inputs=dict.fromkeys(spatial or set(), "location"),
        inputs={
            key: DependencyManifest.from_declaration(dep)
            for key, dep in (inputs or {}).items()
        },
        outputs={key: OutputMetadata(columns=[]) for key in outputs or (name,)},
    )


def manifest(name: str, *nodes: NodeManifest) -> PluginManifest:
    return PluginManifest(
        plugin=PluginInfo(name=name, version="1"),
        factory="unavailable:create_plugin",
        nodes=list(nodes),
    )


def graph(*nodes: NodeManifest) -> PluginGraph:
    return build_graph(
        {
            f"plugin-{index}": manifest(f"plugin-{index}", item).nodes
            for index, item in enumerate(nodes)
        }
    )


def test_converging_graph_and_order_independence() -> None:
    source = node("source", outputs=("first", "second"))
    left = node("left", inputs={"table": Dependency("first")})
    right = node("right", inputs={"table": Dependency("second")})
    root = node(
        "root",
        inputs={"a": Dependency("left"), "b": Dependency("right")},
    )
    for nodes in [(root, right, source, left), (left, source, right, root)]:
        result = graph(*nodes)
        assert len(result.nodes) == 4
        assert result.outputs["first"].producer is result.outputs["second"].producer
        assert result.dependencies["root"]["a"].output.producer.node.name == "left"


@pytest.mark.parametrize(
    "case", ["node", "output", "collision", "missing", "self", "cycle"]
)
def test_invalid_names_and_cycles(case: str) -> None:
    source = node("source", outputs=("table",))
    consumer = node("consumer", inputs={"data": Dependency("table")})
    expected = ""
    if case == "node":
        consumer = node("source", outputs=("different",))
        expected = "Duplicate node"
    elif case == "output":
        consumer = node("consumer", outputs=("table",))
        expected = "Duplicate output"
    elif case == "collision":
        consumer = node("table", outputs=("different",))
        expected = "collides"
    elif case == "missing":
        consumer = node("consumer", inputs={"data": Dependency("source")})
        expected = "consumer.*data.*source.*no enabled producer"
    elif case == "self":
        source = node(
            "source", outputs=("table", "other"), inputs={"loop": Dependency("other")}
        )
        expected = "cycle.*source.*loop.*other.*source"
    else:
        source = node(
            "source", outputs=("table",), inputs={"loop": Dependency("consumer")}
        )
        expected = "cycle.*input.*output.*producer"
    with pytest.raises(ValueError, match=expected):
        graph(source, consumer)


@pytest.mark.parametrize(
    "case",
    [
        "required",
        "default",
        "matching",
        "renamed",
        "literal",
        "target",
        "reference",
        "nested",
    ],
)
def test_parameter_supply_paths(case: str) -> None:
    producer = node("source", parameters=Defaulted if case == "default" else Required)
    parameters = Required if case == "matching" else Renamed
    if case == "nested":
        parameters = Nested
    overrides: dict[str, object] = {}
    if case == "renamed":
        overrides = {"year": Input("parameters.vintage")}
    elif case == "literal":
        overrides = {"year": "runtime validation decides whether this is valid"}
    elif case == "target":
        overrides = {"unknown": 1}
    elif case == "reference":
        overrides = {"year": Input("parameters.missing")}
    consumer = node(
        "consumer",
        parameters=parameters,
        inputs={"data": Dependency("source", arguments={"parameters": overrides})},
    )
    if case in {"required", "target", "reference", "nested"}:
        with pytest.raises(ValueError, match=r"consumer.*data.*source.*producer"):
            graph(producer, consumer)
    else:
        assert graph(producer, consumer).dependencies["consumer"]["data"]


def test_spatial_path_is_required_at_every_edge() -> None:
    source = node("source", spatial={"location"})
    middle = node("middle", inputs={"data": Dependency("source")})
    root = node("root", spatial={"location"}, inputs={"data": Dependency("middle")})
    with pytest.raises(ValueError, match=r"middle.*data.*source.*spatial"):
        graph(root, middle, source)
    wrong = middle.model_copy(update={"spatial_inputs": {"location": "bounds"}})
    with pytest.raises(ValueError, match=r"[Ss]patial"):
        graph(source, wrong)
    middle = node("middle", spatial={"location"}, inputs={"data": Dependency("source")})
    assert graph(root, middle, source)


def test_catalog_graph_is_atomic_and_manifest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = manifest("producer", node("source"))
    consumer = manifest(
        "consumer",
        node(
            "public",
            outputs=("first", "second"),
            inputs={"data": Dependency("source")},
        ),
    )
    paths = []
    for item in [producer, consumer]:
        directory = tmp_path / item.plugin.name
        directory.mkdir()
        (directory / "lyra.plugin.json").write_text(item.model_dump_json())
        VERSIONS[item.plugin.name] = "1"
        paths.append(directory)
    config = load_test_config(tmp_path, plugins=[])
    config.plugins.installed = [plugin_config(path) for path in paths]

    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("Imported plugin implementation")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    catalog = registry.Catalog.load(config.plugins)
    assert catalog.get_loaded_metric_names() == ["public", "source"]
    assert set(catalog.get_plugin_graph().nodes) == {"public", "source"}
    info = catalog.get_metric_info("public")
    assert info is not None
    assert set(info.outputs) == {"first", "second"}
    assert set(catalog.get_loaded_metric_queues()) == {"public", "source"}
    config.plugins.installed[0].enabled = False
    with pytest.raises(ValueError, match="source"):
        registry.Catalog.load(config.plugins)
    config.plugins.installed[0].enabled = True
    config.plugins.installed[0].routing = {"source": "interactive"}
    assert (
        registry.Catalog.load(config.plugins).get_loaded_metric_queues()["source"]
        == "interactive"
    )


@pytest.mark.parametrize("case", ["duplicate", "collision"])
def test_local_manifest_rejects_output_conflicts(case: str) -> None:
    source = node("source", outputs=("table",))
    other = (
        node("other", outputs=("table",))
        if case == "duplicate"
        else node("table", outputs=("different",))
    )
    with pytest.raises(ValueError, match=r"output|Output"):
        manifest("plugin", source, other)


def test_parameter_contract_resolves_reference_chains() -> None:
    source = node("source", parameters=Defaulted)
    schema = source.request_schema
    schema["properties"] = {"parameters": {"$ref": "#/$defs/Alias"}}
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    definitions["Alias"] = {"$ref": "#/$defs/Defaulted"}
    consumer = node("consumer", inputs={"data": Dependency("source")})
    assert graph(consumer, source)
    definitions["Alias"] = {"$ref": "#/$defs/Alias"}
    with pytest.raises(ValueError, match="cyclic parameter schema"):
        graph(consumer, source)


@pytest.mark.parametrize("matching", [False, True])
def test_explicit_location_bindings_and_parameter_overrides(*, matching: bool) -> None:
    consumer = node(
        "consumer",
        spatial={"lots", "location"} if matching else {"lots"},
        parameters=Required,
        inputs={
            "data": Dependency(
                "source",
                arguments={
                    "locations": {"location": Input("lots")},
                    "parameters": {"year": 1990},
                },
            )
        },
    )
    source = node("source", spatial={"location"}, parameters=Required)
    restored = NodeManifest.model_validate_json(consumer.model_dump_json())
    edge = graph(restored, source).dependencies["consumer"]["data"]
    assert edge.dependency.arguments["locations"]["location"] == Input("lots")
    assert edge.dependency.arguments["parameters"]["year"].kind == "literal"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, "no supply path for required spatial input 'location'"),
        ({"locations": {"missing": Input("lots")}}, "unknown location override"),
        ({"locations": {"location": Input("missing")}}, "unknown consumer location"),
        (
            {"locations": {"location": Input("parameters.year")}},
            "requires a location reference",
        ),
        ({"parameters": {"year": Input("lots")}}, "requires a parameter reference"),
        ({"parameters": {"location": 1}}, "unknown parameter override"),
        ({"locations": {"year": Input("lots")}}, "unknown location override"),
    ],
)
def test_invalid_location_supply_paths(
    arguments: dict[str, dict[str, object]], expected: str
) -> None:
    consumer = node(
        "consumer",
        parameters=Required,
        spatial={"lots"},
        inputs={"data": Dependency("source", arguments=arguments)},
    )
    with pytest.raises(ValueError, match=expected) as error:
        graph(consumer, node("source", spatial={"location"}, parameters=Defaulted))
    assert "consumer" in str(error.value)
    assert "input 'data'" in str(error.value)
    assert "producer 'source'" in str(error.value)


def test_invalid_explicit_bindings_do_not_fall_back_to_matching_names() -> None:
    consumer = node(
        "consumer",
        parameters=Required,
        spatial={"location"},
        inputs={
            "data": Dependency(
                "source",
                arguments={
                    "locations": {"location": Input("missing")},
                    "parameters": {"year": Input("parameters.missing")},
                },
            )
        },
    )
    with pytest.raises(
        ValueError, match=r"unknown consumer parameter.*unknown consumer location"
    ):
        graph(consumer, node("source", spatial={"location"}, parameters=Defaulted))


def test_location_free_producer_with_forwarding_only_consumer() -> None:
    assert graph(
        node("source", parameters=Defaulted),
        node("consumer", spatial={"lots"}, inputs={"data": Dependency("source")}),
    )


@pytest.mark.parametrize(
    "literal",
    [
        None,
        ["lot-id"],
        {"type": "FeatureCollection"},
        {"kind": "input", "path": "lots"},
    ],
)
def test_location_literals_rejected_in_declarations_and_manifests(
    literal: object,
) -> None:
    with pytest.raises(ValueError, match="Location bindings require Input"):
        Dependency("source", arguments={"locations": {"location": literal}})
    with pytest.raises(ValueError, match="Location bindings require Input"):
        DependencyManifest.model_validate(
            {
                "output": "source",
                "arguments": {
                    "locations": {"location": {"kind": "literal", "value": literal}}
                },
            }
        )


def test_location_reference_roundtrip_distinguishes_parameter_literal() -> None:
    literal = {"kind": "input", "path": "lots"}
    dependency = DependencyManifest.from_declaration(
        Dependency(
            "source",
            arguments={
                "locations": {"location": Input("lots")},
                "parameters": {"settings": literal},
            },
        )
    )
    restored = DependencyManifest.model_validate_json(dependency.model_dump_json())
    assert restored == dependency
    assert restored.arguments["locations"]["location"] == Input("lots")
    value = restored.arguments["parameters"]["settings"]
    assert isinstance(value, LiteralArgument)
    assert value.model_dump() == {
        "kind": "literal",
        "value": literal,
    }
