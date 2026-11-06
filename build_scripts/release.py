"""Validate and plan Lyra releases from Release Please manifests."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- invokes Git
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = "release-please-config.json"
MANIFEST_PATH = ".release-please-manifest.json"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


class ReleasePlanError(ValueError):
    """Raised when repository release state is incomplete or inconsistent."""


@dataclass(frozen=True)
class ReleasePlan:
    """Files and GitHub Actions outputs generated for a release."""

    version: str
    tag: str
    commit: str
    notes_path: Path
    manifest_path: Path


def _object_dict(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"{description} must be a JSON/TOML object"
        raise ReleasePlanError(message)
    return cast("dict[str, object]", value)


def _json_object(text: str, description: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"{description} is not valid JSON: {error}"
        raise ReleasePlanError(message) from error
    return _object_dict(value, description)


def _toml_object(text: str, description: str) -> dict[str, object]:
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        message = f"{description} is not valid TOML: {error}"
        raise ReleasePlanError(message) from error
    return _object_dict(value, description)


def _git(root: Path, command: str, ref: str) -> str:
    git = shutil.which("git")
    if git is None:
        message = "git is required to plan a release"
        raise ReleasePlanError(message)
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true] -- refs are validated commit SHAs
        [git, command, ref],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = f"git {command} {ref}: {result.stderr.strip()}"
        raise ReleasePlanError(message)
    return result.stdout


def _validate_config(config_text: str) -> None:
    config = _json_object(config_text, CONFIG_PATH)
    packages = _object_dict(config.get("packages"), "packages")
    if packages.keys() != {"."}:
        message = "Only the root Lyra project may be released"
        raise ReleasePlanError(message)
    settings = config | _object_dict(packages["."], "root package")
    required = {
        "release-type": "python",
        "package-name": "lyra",
        "skip-github-release": True,
        "include-component-in-tag": False,
        "exclude-paths": ["docs/**"],
    }
    for key, expected in required.items():
        if settings.get(key) != expected:
            message = f"Expected {key} = {expected!r} in {CONFIG_PATH}"
            raise ReleasePlanError(message)
    if (
        settings.get("changelog-path", "CHANGELOG.md") != "CHANGELOG.md"
        or settings.get("include-v-in-tag", True) is not True
    ):
        message = "Lyra releases require CHANGELOG.md and vX.Y.Z tags"
        raise ReleasePlanError(message)


def _manifest_version(text: str) -> str:
    manifest = _json_object(text, MANIFEST_PATH)
    if manifest.keys() != {"."}:
        message = f"{MANIFEST_PATH} must contain only the root version"
        raise ReleasePlanError(message)
    version = manifest["."]
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        message = f"Invalid release version: {version!r}"
        raise ReleasePlanError(message)
    return version


def _changelog_body(changelog: str, version: str, path: str) -> str:
    lines = changelog.splitlines()
    heading = re.compile(
        rf"^## \[?{re.escape(version)}\]?(?:\([^)]*\))?(?: \([^)]*\))?$"
    )
    start: int | None = None
    for index, line in enumerate(lines):
        if heading.fullmatch(line):
            start = index + 1
            break
    if start is None:
        message = f"{path} has no release entry for {version}"
        raise ReleasePlanError(message)
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    body = "\n".join(lines[start:end]).strip()
    if not body:
        message = f"{path} has an empty release entry for {version}"
        raise ReleasePlanError(message)
    return body


def _release_metadata(read_text: Callable[[str], str]) -> tuple[str, str]:
    _validate_config(read_text(CONFIG_PATH))
    version = _manifest_version(read_text(MANIFEST_PATH))
    metadata = _toml_object(read_text("pyproject.toml"), "pyproject.toml")
    project = _object_dict(metadata.get("project"), "project")
    if project.get("name") != "lyra" or project.get("version") != version:
        message = f"Expected pyproject.toml to declare lyra version {version}"
        raise ReleasePlanError(message)
    notes = _changelog_body(read_text("CHANGELOG.md"), version, "CHANGELOG.md")
    return version, notes


def validate_repository(root: Path = ROOT) -> None:
    """Validate the root release configuration, metadata and changelog entry."""
    _release_metadata(lambda path: (root / path).read_text(encoding="utf-8"))


def _write_github_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")


def plan_release(
    base_ref: str,
    head_ref: str,
    output_directory: Path,
    *,
    root: Path = ROOT,
    github_output: Path | None = None,
) -> ReleasePlan:
    """Plan a Lyra release from two immutable repository revisions.

    Returns:
        The product release metadata and paths of generated release artifacts.

    Raises:
        ReleasePlanError: If refs, package state, version changes, or changelogs do
            not satisfy the release contract.
    """
    for name, ref in (("base", base_ref), ("head", head_ref)):
        if COMMIT_PATTERN.fullmatch(ref) is None:
            message = f"{name} ref must be a full lowercase commit SHA"
            raise ReleasePlanError(message)
        if _git(root, "rev-parse", f"{ref}^{{commit}}").strip() != ref:
            message = f"{name} ref must identify the commit itself"
            raise ReleasePlanError(message)
    before = _manifest_version(_git(root, "show", f"{base_ref}:{MANIFEST_PATH}"))
    version, notes = _release_metadata(
        lambda path: _git(root, "show", f"{head_ref}:{path}")
    )
    if tuple(map(int, version.split("."))) <= tuple(map(int, before.split("."))):
        message = f"The release version must increase from {before}, got {version}"
        raise ReleasePlanError(message)

    tag = f"v{version}"
    manifest_path = output_directory / "release-manifest.json"
    notes_path = output_directory / "release-notes.md"
    identity = {"version": version, "tag": tag, "commit": head_ref}
    manifest = {"schema_version": 2, "name": "lyra", **identity}
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")
    notes_path.write_text(f"{notes}\n", encoding="utf-8")
    if github_output is not None:
        _write_github_outputs(github_output, identity)
    return ReleasePlan(version, tag, head_ref, notes_path, manifest_path)


def build_parser() -> argparse.ArgumentParser:
    """Build the release tooling command-line parser.

    Returns:
        The parser for repository validation and release planning commands.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate current release metadata")
    plan = subparsers.add_parser("plan", help="plan a release from two Git refs")
    plan.add_argument("--base-ref", required=True)
    plan.add_argument("--head-ref", required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    plan.add_argument("--github-output", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    """Run release validation or planning from the command line.

    Returns:
        Zero on success or two when release metadata is invalid.
    """
    options = build_parser().parse_args(arguments)
    try:
        if options.command == "validate":
            validate_repository()
        else:
            plan_release(
                options.base_ref,
                options.head_ref,
                options.output_dir,
                github_output=options.github_output,
            )
    except ReleasePlanError as error:
        sys.stderr.write(f"release error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
