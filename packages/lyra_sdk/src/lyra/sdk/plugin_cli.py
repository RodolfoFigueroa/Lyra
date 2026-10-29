"""Command-line entry points for inspecting and validating plugins."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.sdk.diagrams import render_mermaid
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from lyra.sdk.plugin_loader import PluginLoadError, load_plugin_definition

if TYPE_CHECKING:
    from lyra.sdk.plugin import NodeDescription, PluginDefinition

MANIFEST_FILENAME = "lyra.plugin.json"


class PluginBuildError(RuntimeError):
    """Raised when project metadata cannot produce a plugin manifest."""


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

    tool = payload.get("tool")
    lyra = tool.get("lyra") if isinstance(tool, dict) else None
    factory = lyra.get("factory") if isinstance(lyra, dict) else None
    if not isinstance(factory, str) or not factory:
        msg = "pyproject.toml must define [tool.lyra].factory as 'module:attribute'"
        raise PluginBuildError(msg)
    return name, version, factory


def _load_definition(project_root: Path, factory: str) -> PluginDefinition:
    import_paths = [project_root, project_root / "src"]
    for import_path in reversed(import_paths):
        path = str(import_path)
        if import_path.is_dir() and path not in sys.path:
            sys.path.insert(0, path)
    try:
        return load_plugin_definition(factory)
    except PluginLoadError as exc:
        msg = str(exc)
        raise PluginBuildError(msg) from exc


def render_manifest(project_root: Path) -> str:
    """Build the canonical manifest text for one plugin project.

    Returns:
        The validated authoring manifest as newline-terminated JSON.
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


def build_manifest(project_root: Path) -> Path:
    """Write the canonical plugin manifest when its contents have changed.

    Returns:
        The path to the project's generated manifest.
    """
    content = render_manifest(project_root)
    manifest_path = project_root.resolve() / MANIFEST_FILENAME
    if (
        not manifest_path.is_file()
        or manifest_path.read_text(encoding="utf-8") != content
    ):
        _write_atomic(manifest_path, content)
    return manifest_path


def check_manifest(project_root: Path) -> tuple[bool, str]:
    """Compare a checked-in plugin manifest with its canonical rendering.

    Returns:
        A match flag and an empty string or unified diff describing changes.
    """
    expected = render_manifest(project_root)
    manifest_path = project_root.resolve() / MANIFEST_FILENAME
    actual = (
        manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else ""
    )
    if actual == expected:
        return True, ""
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(manifest_path),
            tofile=f"generated:{manifest_path}",
        )
    )
    return False, diff


def describe_plugin(
    project_root: Path,
    metric_name: str | None = None,
) -> list[NodeDescription]:
    """Load and describe one or all nodes in a plugin project.

    Returns:
        Authoring descriptions for the selected registered metrics.
    """
    project_root = project_root.resolve()
    _name, _version, factory = _project_configuration(project_root)
    definition = _load_definition(project_root, factory)
    names = definition.node_names if metric_name is None else (metric_name,)
    return [definition.describe(name) for name in names]


def render_description(
    project_root: Path,
    metric_name: str | None = None,
    *,
    json_output: bool = False,
) -> str:
    """Render deterministic author-facing metric information.

    Returns:
        Newline-terminated JSON or human-readable metric details.
    """
    project_root = project_root.resolve()
    _name, _version, factory = _project_configuration(project_root)
    definition = _load_definition(project_root, factory)
    names = definition.node_names if metric_name is None else (metric_name,)
    descriptions = [definition.describe(name) for name in names]
    provider = definition.location_provider
    if json_output:
        payload = {
            "nodes": [
                description.model_dump(mode="json", exclude_unset=True)
                for description in descriptions
            ]
        }
        if provider is not None:
            payload["location_provider"] = provider.model_dump(mode="json")
        return f"{json.dumps(payload, indent=2)}\n"
    rendered = "\n\n".join(_render_node(description) for description in descriptions)
    if provider is not None:
        rendered += "\nLocation provider:\n" + json.dumps(
            provider.model_dump(mode="json"), indent=2
        )
    return f"{rendered}\n"


def _render_node(description: NodeDescription) -> str:
    return "\n".join(
        [
            f"Metric: {description.name}",
            f"Description: {description.description}",
            f"Handler: {description.handler}",
            f"Signature: {description.signature}",
            "Spatial inputs: " + ", ".join(description.spatial_inputs),
            "Dependencies:",
            json.dumps(
                {
                    name: dep.model_dump(mode="json")
                    for name, dep in description.inputs.items()
                },
                indent=2,
                sort_keys=True,
            ),
            "Request schema:",
            json.dumps(description.request_schema, indent=2, sort_keys=True),
            *(
                f"Output {name}: table ({len(output.columns)} source column(s))"
                for name, output in description.outputs.items()
            ),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for plugin authoring operations.

    Returns:
        The configured top-level argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="lyra-plugin",
        description="Build, verify, and inspect Lyra plugin definitions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build-manifest", "check-manifest"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--project",
            type=Path,
            default=Path.cwd(),
            help="Plugin project root (default: current directory).",
        )
    diagram_parser = subparsers.add_parser("diagram")
    diagram_parser.add_argument("--project", type=Path, default=Path.cwd())
    diagram_parser.add_argument(
        "--include-manifest", type=Path, action="append", default=[]
    )
    diagram_parser.add_argument("--output", type=Path)
    diagram_parser.add_argument(
        "--detailed",
        action="store_true",
        help="Include metric dependency inputs and output column metadata.",
    )
    describe_parser = subparsers.add_parser("describe")
    describe_parser.add_argument(
        "metric",
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
    return parser


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
    if args.command == "diagram":
        paths = [args.project / MANIFEST_FILENAME, *args.include_manifest]
        if args.output is not None and args.output.resolve() in {
            path.resolve() for path in paths
        }:
            msg = "Diagram output must not overwrite an input manifest"
            raise PluginBuildError(msg)
        manifests = [
            PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))
            for path in paths
        ]
        content = render_mermaid(manifests, detailed=args.detailed)
        if args.output is None:
            sys.stdout.write(content)
        else:
            _write_atomic(args.output, content)
        return 0
    if args.command == "build-manifest":
        path = build_manifest(args.project)
        sys.stdout.write(f"{path}\n")
        return 0
    if args.command == "describe":
        sys.stdout.write(
            render_description(
                args.project,
                args.metric,
                json_output=args.json_output,
            )
        )
        return 0
    valid, diff = check_manifest(args.project)
    if valid:
        return 0
    sys.stderr.write(diff)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
