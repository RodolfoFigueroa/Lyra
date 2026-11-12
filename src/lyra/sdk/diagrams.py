"""Render derived declarations as Mermaid inspection diagrams."""

import json
import re
from collections.abc import Sequence
from operator import itemgetter

from lyra.contracts.plugins import PluginInspection, PluginManifest
from lyra.contracts.schema import build_node_request_schema


def _label(value: str) -> str:
    return "".join(
        character
        if character.isalnum() or character in " _:-"
        else f"#{ord(character)};"
        for character in value
    )


def render_mermaid(
    manifests: Sequence[PluginManifest | PluginInspection], *, detailed: bool = False
) -> str:
    """Render declared dependencies, including cycles and external outputs.

    Args:
        manifests: Derived plugin descriptions to inspect.
        detailed: Include node inputs, bindings, resources, and output metadata.

    Returns:
        Deterministic, newline-terminated Mermaid flowchart source.

    Raises:
        ValueError: If input is empty or declarations have ambiguous ownership.
    """
    if not manifests:
        msg = "At least one plugin description is required"
        raise ValueError(msg)
    plugins = sorted(
        [
            PluginInspection.model_validate_json(item.model_dump_json())
            for item in manifests
        ],
        key=lambda item: item.plugin.name,
    )
    identities = [re.sub(r"[-_.]+", "-", item.plugin.name).lower() for item in plugins]
    if len(set(identities)) != len(identities):
        msg = "Duplicate plugin identity"
        raise ValueError(msg)
    nodes, outputs = _index(plugins)
    output_sources = {
        name: f"{producer}.{name[1]}" for name, producer in outputs.items()
    }
    ids = {name: f"n{index}" for index, name in enumerate(sorted(nodes))}
    external = sorted(
        {
            (dep["producer"], dep["output"])
            for node in nodes.values()
            for dep in node["inputs"].values()
        }
        - outputs.keys()
    )
    external_ids = {name: f"e{index}" for index, name in enumerate(external)}
    lines = ["flowchart LR"]
    for index, plugin in enumerate(plugins):
        grouped = len(plugins) > 1
        if grouped:
            lines.append(f'    subgraph p{index}["{_label(plugin.plugin.name)}"]')
        for node in sorted(plugin.nodes, key=itemgetter("name")):
            label = _node_label(node, output_sources, detailed=detailed)
            lines.append(f'    {ids[node["name"]]}["{label}"]')
        if grouped:
            lines.append("    end")
    for name, identifier in external_ids.items():
        lines.extend(
            [
                f'    {identifier}["External output: {_label(name[1])}"]',
                f"    style {identifier} stroke-dasharray: 5 5",
            ]
        )
    lines.extend(_render_location_edges(nodes, ids))
    for name in sorted(nodes):
        for input_name, dependency in sorted(nodes[name]["inputs"].items()):
            output = (dependency["producer"], dependency["output"])
            producer = (
                ids[outputs[output]] if output in outputs else external_ids[output]
            )
            label = _label(f"{dependency['output']} → {input_name}")
            lines.append(f'    {producer} -->|"{label}"| {ids[name]}')
    return "\n".join(lines) + "\n"


def _render_location_edges(nodes: dict[str, dict], ids: dict[str, str]) -> list[str]:
    location_edges = [
        (name, path, operation)
        for name, node in sorted(nodes.items())
        for input_name, dependency in sorted(node["inputs"].items())
        for path, operation in _location_edges(dependency, f"inputs.{input_name}")
    ]
    external_operations = sorted(
        {operation for _, _, operation in location_edges} - nodes.keys()
    )
    operation_ids = {
        name: f"l{index}" for index, name in enumerate(external_operations)
    }
    lines = []
    for name, identifier in operation_ids.items():
        lines.extend(
            [
                f'    {identifier}["External location operation: {_label(name)}"]',
                f"    style {identifier} stroke-dasharray: 5 5",
            ]
        )
    for owner, path, operation in location_edges:
        producer = ids[operation] if operation in ids else operation_ids[operation]
        label = _label(f"locations: {operation} → {path}")
        lines.append(f'    {producer} -.->|"{label}"| {ids[owner]}')
    return lines


def _location_edges(dependency: dict, path: str) -> list[tuple[str, str]]:
    edges = []
    for name, binding in sorted(dependency["locations"].items()):
        if "operation" in binding:
            receiving = f"{path}.locations.{name}"
            edges.append((receiving, binding["operation"]))
            edges.extend(_location_edges(binding, receiving))
    return edges


def _index(
    plugins: Sequence[PluginInspection],
) -> tuple[dict[str, dict], dict[tuple[str, str], str]]:
    nodes = {}
    outputs = {}
    for plugin in plugins:
        for node in plugin.nodes:
            if node["name"] in nodes:
                msg = f"Duplicate node {node['name']!r}"
                raise ValueError(msg)
            nodes[node["name"]] = node
            for output in node["outputs"] if node["kind"] == "metric" else ():
                outputs[node["name"], output] = node["name"]
    return nodes, outputs


def _node_label(
    node: dict, output_sources: dict[tuple[str, str], str], *, detailed: bool
) -> str:
    kind = "Metric" if node["kind"] == "metric" else "Location operation"
    title = _label(f"{kind}: {node['name']}")
    lines = [f"<b>{title}</b>", ""]
    if detailed and node["inputs"]:
        lines.append("<b>Inputs</b>")
        lines.extend(
            _label(
                f"•\N{NO-BREAK SPACE}{name}:\N{NO-BREAK SPACE}"
                + output_sources.get(
                    (dependency["producer"], dependency["output"]), dependency["output"]
                )
            )
            for name, dependency in sorted(node["inputs"].items())
        )
        lines.append("")
    for index, (name, output) in enumerate(
        sorted(node["outputs"].items() if node["kind"] == "metric" else ())
    ):
        if index:
            lines.append("")
        lines.append(f"<b>{_label(f'Output: {name}')}</b>")
        if detailed:
            for column in output["columns"]:
                if column["name"] == output["geometry"]:
                    continue
                metadata = (
                    f"[{column['unit']}]"
                    if column["unit"] is not None
                    else column["dtype"]
                )
                lines.append(
                    _label(
                        f"•\N{NO-BREAK SPACE}{column['name']}:"
                        f"\N{NO-BREAK SPACE}{metadata}"
                    )
                )
            if output["geometry"] is not None:
                lines.append(
                    _label(
                        f"•\N{NO-BREAK SPACE}Geometry:"
                        f"\N{NO-BREAK SPACE}{output['geometry']}"
                    )
                )
    lines.extend(_contract_details(node, detailed=detailed))
    return "<br/>".join(lines)


def _contract_details(node: dict, *, detailed: bool) -> list[str]:
    lines = []
    if node["kind"] == "location_operation":
        lines.extend(
            [
                "<b>Location output</b>",
                _label(
                    f"ID: {node['output_contract']['family']['structure']['id_column']}"
                ),
            ]
        )
        scope = {
            key: value
            for key, value in node["output_contract"]["family"].items()
            if key != "structure"
        }
        lines.append(_label(f"Scope: {scope}"))
        if detailed:
            lines.append(
                _label(
                    json.dumps(
                        node["output_contract"]["family"]["structure"]["output"],
                        sort_keys=True,
                    )
                )
            )
    if detailed:
        metadata = {
            "Location inputs": node["location_inputs"],
            "Resources": node["resources"],
            "Request schema": build_node_request_schema(
                node["name"],
                node["parameters_schema"],
                {
                    name: value["description"]
                    for name, value in node["location_inputs"].items()
                },
            )
            if node["kind"] == "location_operation"
            else {},
            "Bindings": {
                name: dependency
                for name, dependency in sorted(node["inputs"].items())
                if dependency["locations"] or dependency["parameters"]
            },
        }
        for heading, value in metadata.items():
            if value:
                lines.extend(
                    [f"<b>{heading}</b>", _label(json.dumps(value, sort_keys=True))]
                )
    return lines
