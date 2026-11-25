"""Command-line entry points for inspecting and validating plugins."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.contracts.config import DEFAULT_API_PORT, normalize_distribution_name
from lyra.contracts.dependencies import describe_dependency
from lyra.contracts.plugins import PluginInfo, PluginInspection
from lyra.runtime.planning.native_bindings import bind_self_contained
from lyra.sdk import dev
from lyra.sdk.diagrams import render_mermaid
from lyra.sdk.loading import PluginLoadError, load_plugin_definition

if TYPE_CHECKING:
    from lyra.sdk.plugin import NodeDescription, PluginDefinition


class PluginBuildError(RuntimeError):
    """Raised when project metadata cannot load a plugin definition."""


def _project_configuration(project_root: Path) -> tuple[str, str, str]:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.is_file():
        msg = f"Plugin project is missing {pyproject_path}"
        raise PluginBuildError(msg)
    with pyproject_path.open("rb") as pyproject_file:
        payload = tomllib.load(pyproject_file)

    project = payload.get("project")
    if not isinstance(project, dict):
        msg = "pyproject.toml must contain a [project] table"
        raise PluginBuildError(msg)
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        msg = "[project].name must be a non-empty string"
        raise PluginBuildError(msg)
    if not isinstance(version, str) or not version:
        msg = "[project].version must be a static non-empty string"
        raise PluginBuildError(msg)

    entry_points = project.get("entry-points", {})
    group = (
        entry_points.get("lyra.plugins", {}) if isinstance(entry_points, dict) else {}
    )
    factory = group.get("plugin") if isinstance(group, dict) else None
    if not isinstance(factory, str) or not factory:
        msg = 'pyproject.toml must define [project.entry-points."lyra.plugins"].plugin'
        raise PluginBuildError(msg)
    return name, version, factory


def _load_definition(project_root: Path, factory: str) -> PluginDefinition:
    import_paths = [project_root, project_root / "src"]
    for import_path in reversed(import_paths):
        path = str(import_path)
        if import_path.is_dir() and path not in sys.path:
            sys.path.insert(0, path)
    try:
        return bind_self_contained(load_plugin_definition(factory))
    except PluginLoadError as exc:
        msg = str(exc)
        raise PluginBuildError(msg) from exc


def render_manifest(project_root: Path) -> str:
    """Export derived inspection JSON from a live plugin project.

    Returns:
        The detached inspection data as newline-terminated JSON.
    """
    project_root = project_root.resolve()
    name, version, factory = _project_configuration(project_root)
    definition = _load_definition(project_root, factory)
    manifest = definition.manifest(
        plugin=PluginInfo(name=name, version=version),
        factory=factory,
    )
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def describe_plugin(
    project_root: Path,
    node_name: str | None = None,
) -> list[NodeDescription]:
    """Load and describe one or all nodes in a plugin project.

    Returns:
        Authoring descriptions for the selected metrics and location operations.
    """
    project_root = project_root.resolve()
    _name, _version, factory = _project_configuration(project_root)
    definition = _load_definition(project_root, factory)
    names = definition.node_names if node_name is None else (node_name,)
    return [definition.describe(name) for name in names]


def render_description(
    project_root: Path,
    node_name: str | None = None,
    *,
    json_output: bool = False,
) -> str:
    """Render deterministic author-facing node information.

    Returns:
        Newline-terminated JSON or human-readable node details.
    """
    project_root = project_root.resolve()
    _name, _version, factory = _project_configuration(project_root)
    definition = _load_definition(project_root, factory)
    names = definition.node_names if node_name is None else (node_name,)
    descriptions = [definition.describe(name) for name in names]
    providers = definition.providers
    if json_output:
        payload = {
            "nodes": [
                {
                    **description.model_dump(mode="json"),
                    "request_schema": description.manifest.request_schema,
                }
                for description in descriptions
            ]
        }
        if providers:
            payload["providers"] = [
                provider.model_dump(mode="json") for provider in providers
            ]
        return f"{json.dumps(payload, indent=2)}\n"
    rendered = "\n\n".join(_render_node(description) for description in descriptions)
    if providers:
        rendered += "\nLocation providers:\n" + json.dumps(
            [provider.model_dump(mode="json") for provider in providers], indent=2
        )
    return f"{rendered}\n"


def _render_node(description: NodeDescription) -> str:
    kind = "Metric" if description.manifest.kind == "metric" else "Location operation"
    return "\n".join(
        [
            f"{kind}: {description.manifest.name}",
            f"Description: {description.manifest.description}",
            f"Handler: {description.handler}",
            f"Signature: {description.signature}",
            "Spatial inputs: " + ", ".join(description.manifest.location_inputs),
            json.dumps(
                {
                    name: value.model_dump(mode="json")
                    for name, value in description.manifest.location_inputs.items()
                },
                indent=2,
                sort_keys=True,
            ),
            *(
                [
                    "Direct origins:",
                    json.dumps(
                        sorted(
                            (
                                origin.model_dump(mode="json")
                                for origin in description.manifest.origins
                            ),
                            key=lambda origin: json.dumps(origin, sort_keys=True),
                        ),
                        indent=2,
                    ),
                ]
                if description.manifest.kind == "metric"
                else []
            ),
            "Dependencies:",
            json.dumps(
                {
                    name: describe_dependency(dep)
                    for name, dep in description.manifest.inputs.items()
                },
                indent=2,
                sort_keys=True,
            ),
            "Resources:",
            json.dumps(description.manifest.resources, indent=2, sort_keys=True),
            "Request schema:",
            json.dumps(description.manifest.request_schema, indent=2, sort_keys=True),
            *(
                [
                    "Location output:",
                    json.dumps(
                        description.manifest.output_contract.model_dump(mode="json"),
                        indent=2,
                        sort_keys=True,
                    ),
                ]
                if description.manifest.kind == "location_operation"
                else []
            ),
            *(
                f"Output {name}: table ({len(output.columns)} source column(s))"
                for name, output in (
                    description.manifest.outputs.items()
                    if description.manifest.kind == "metric"
                    else ()
                )
            ),
        ]
    )


def build_parsers() -> list[argparse.ArgumentParser]:
    """Build root and subcommand parsers for execution and documentation.

    Returns:
        All parsers in command order, with the root first.
    """
    parser = argparse.ArgumentParser(
        prog="lyra-plugin",
        description="Develop and inspect Lyra plugins.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    parsers = [parser]
    diagram_parser = subparsers.add_parser("diagram")
    diagram_parser.add_argument("--project", type=Path, default=Path.cwd())
    diagram_parser.add_argument(
        "--include-project", type=Path, action="append", default=[]
    )
    diagram_parser.add_argument("--output", type=Path)
    diagram_parser.add_argument(
        "--detailed",
        action="store_true",
        help="Include node inputs, bindings, resources, and output metadata.",
    )
    describe_parser = subparsers.add_parser("describe")
    describe_parser.add_argument(
        "node",
        nargs="?",
        help="Node name (default: describe every registered node).",
    )
    describe_parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Plugin project root (default: current directory).",
    )
    describe_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit structured JSON instead of a human-readable table.",
    )
    develop = subparsers.add_parser(
        "dev", help="Run a local API and worker with editable plugins."
    )
    develop.add_argument("--project", type=Path, default=Path.cwd())
    develop.add_argument(
        "--plugin",
        type=Path,
        action="append",
        default=[],
        help="Additional plugin checkout (repeatable).",
    )
    develop.add_argument(
        "--valkey-url",
        help="Use an external Valkey endpoint and clear this checkout's waiting queue.",
    )
    develop.add_argument(
        "--port",
        type=int,
        default=DEFAULT_API_PORT,
        help="Loopback API port (default: %(default)s).",
    )
    develop.add_argument(
        "--resources", type=Path, help="TOML resource settings for the worker."
    )
    return [*parsers, diagram_parser, describe_parser, develop]


def build_parser() -> argparse.ArgumentParser:
    """Build the executable plugin authoring parser.

    Returns:
        The root parser with all supported subcommands.
    """
    return build_parsers()[0]


def main(argv: list[str] | None = None) -> int:
    """Run the plugin authoring command-line interface.

    Returns:
        Zero on success or two for an invalid plugin or command operation.
    """
    args = build_parser().parse_args(argv)
    try:
        return _run_command(args)
    except (PluginBuildError, OSError, ValueError) as exc:
        sys.stderr.write(f"lyra-plugin: {exc}\n")
        return 2


def _run_command(args: argparse.Namespace) -> int:
    if args.command == "dev":
        projects = {}
        for path in [args.project, *args.plugin]:
            distribution, _, _ = _project_configuration(path)
            name = normalize_distribution_name(distribution)
            if name in projects:
                msg = f"Duplicate plugin distribution: {name}"
                raise PluginBuildError(msg)
            projects[name] = path.resolve()
        return dev.launch(projects, args.port, args.resources, args.valkey_url)
    if args.command == "diagram":
        paths = [args.project, *args.include_project]
        if args.output is not None and any(
            args.output.resolve().is_relative_to(path.resolve())
            and args.output.suffix in {".py", ".toml"}
            for path in paths
        ):
            msg = "Diagram output must not overwrite plugin source or configuration"
            raise PluginBuildError(msg)
        manifests = [
            PluginInspection.model_validate_json(render_manifest(path))
            for path in paths
        ]
        content = render_mermaid(manifests, detailed=args.detailed)
        if args.output is None:
            sys.stdout.write(content)
        else:
            _write_atomic(args.output, content)
        return 0
    if args.command == "describe":
        sys.stdout.write(
            render_description(
                args.project,
                args.node,
                json_output=args.json_output,
            )
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
