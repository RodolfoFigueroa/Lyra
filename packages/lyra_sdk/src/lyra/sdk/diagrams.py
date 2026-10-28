"""Render manifest declarations as Mermaid inspection diagrams."""

import re
from collections.abc import Sequence

from lyra.sdk.models.plugin import NodeManifest, PluginManifest


def _label(value: str) -> str:
    return "".join(
        character
        if character.isalnum() or character in " _:-"
        else f"#{ord(character)};"
        for character in value
    )


def render_mermaid(manifests: Sequence[PluginManifest]) -> str:
    """Render declared dependencies, including cycles and external outputs.

    Args:
        manifests: Saved plugin contracts to inspect without loading factories.

    Returns:
        Deterministic, newline-terminated Mermaid flowchart source.

    Raises:
        ValueError: If input is empty or declarations have ambiguous ownership.
    """
    if not manifests:
        msg = "At least one plugin manifest is required"
        raise ValueError(msg)
    plugins = sorted(manifests, key=lambda item: item.plugin.name)
    identities = [re.sub(r"[-_.]+", "-", item.plugin.name).lower() for item in plugins]
    if len(set(identities)) != len(identities):
        msg = "Duplicate plugin identity"
        raise ValueError(msg)
    nodes, outputs = _index(plugins)
    ids = {name: f"n{index}" for index, name in enumerate(sorted(nodes))}
    external = sorted(
        {dep.output for node in nodes.values() for dep in node.inputs.values()}
        - outputs.keys()
    )
    external_ids = {name: f"e{index}" for index, name in enumerate(external)}
    lines = ["flowchart LR"]
    for index, plugin in enumerate(plugins):
        grouped = len(plugins) > 1
        if grouped:
            lines.append(f'    subgraph p{index}["{_label(plugin.plugin.name)}"]')
        for node in sorted(plugin.nodes, key=lambda item: item.name):
            label = _label(f"{node.role.capitalize()}: {node.name}")
            shape = f'(["{label}"])' if node.role == "dataset" else f'["{label}"]'
            lines.append(f"    {ids[node.name]}{shape}")
        if grouped:
            lines.append("    end")
    for name, identifier in external_ids.items():
        lines.extend(
            [
                f'    {identifier}["External output: {_label(name)}"]',
                f"    style {identifier} stroke-dasharray: 5 5",
            ]
        )
    for name in sorted(nodes):
        for input_name, dependency in sorted(nodes[name].inputs.items()):
            output = dependency.output
            producer = (
                ids[outputs[output]] if output in outputs else external_ids[output]
            )
            label = _label(f"{output} → {input_name}")
            lines.append(f'    {producer} -->|"{label}"| {ids[name]}')
    return "\n".join(lines) + "\n"


def _index(
    plugins: Sequence[PluginManifest],
) -> tuple[dict[str, NodeManifest], dict[str, str]]:
    nodes = {}
    outputs = {}
    for plugin in plugins:
        for node in plugin.nodes:
            if node.name in nodes:
                msg = f"Duplicate node {node.name!r}"
                raise ValueError(msg)
            nodes[node.name] = node
            for output in node.outputs:
                if output in outputs:
                    msg = f"Duplicate output {output!r}"
                    raise ValueError(msg)
                outputs[output] = node.name
    for output, producer in outputs.items():
        if output in nodes and output != producer:
            msg = f"Output {output!r} collides with another node"
            raise ValueError(msg)
    return nodes, outputs
