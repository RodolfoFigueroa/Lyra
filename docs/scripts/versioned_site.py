"""Build one GitHub Pages tree from main and every supported product tag."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- invokes Git/npm
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG_PATTERN = re.compile(r"^lyra(?:-app)?-v(?P<version>\d+\.\d+\.\d+)$")
ARCHIVE_RECIPE_VERSION = "1"
MINIMUM_VERSION = (0, 6, 0)


class VersionedSiteError(ValueError):
    """Raised when a versioned site cannot be assembled safely."""


@dataclass(frozen=True, order=True)
class Release:
    """Identify one supported product release by semantic version and Git tag."""

    version: tuple[int, int, int]
    tag: str

    @property
    def label(self) -> str:
        """The release version formatted as ``major.minor.patch``."""
        return ".".join(str(part) for part in self.version)

    @property
    def base(self) -> str:
        """The GitHub Pages base path reserved for this release."""
        return f"/lyra/versions/{self.label}"


def parse_release(tag: str) -> Release | None:
    """Parse a supported Lyra product tag into release metadata.

    Returns:
        The parsed release, or ``None`` for unrelated or unsupported tags.
    """
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.group("version").split("."))
    version = (major, minor, patch)
    if version < MINIMUM_VERSION:
        return None
    return Release(version=version, tag=tag)


def discover_releases() -> list[Release]:
    """Discover supported product releases from the repository's Git tags.

    Returns:
        Releases in newest-first order, preferring current ``lyra-v`` tag names.
    """
    result = subprocess.run(
        ["git", "tag", "--list", "lyra-v*", "lyra-app-v*"],  # ruff:ignore[start-process-with-partial-path]
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    releases_by_version: dict[tuple[int, int, int], Release] = {}
    for tag in result.stdout.splitlines():
        release = parse_release(tag)
        if release is None:
            continue
        previous = releases_by_version.get(release.version)
        if previous is None or release.tag.startswith("lyra-v"):
            releases_by_version[release.version] = release
    return sorted(releases_by_version.values(), reverse=True)


def run(
    *command: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a checked command in a selected directory and environment.

    Returns:
        The successfully completed subprocess result.
    """
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true] -- commands are constructed only in this module
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def patch_historical_base(config_path: Path, base: str) -> None:
    """Patch an older Astro config that predates environment-based base paths.

    Raises:
        VersionedSiteError: If the expected historical base declaration is absent.
    """
    content = config_path.read_text(encoding="utf-8")
    if "LYRA_DOCS_BASE" in content:
        return
    updated, replacements = re.subn(
        r"(?m)^(\s*)base:\s*['\"]/lyra['\"],",
        rf"\1base: '{base}',",
        content,
        count=1,
    )
    if replacements != 1:
        message = f"Could not set the historical docs base in {config_path}"
        raise VersionedSiteError(message)
    config_path.write_text(updated, encoding="utf-8")


def build_ref(ref: str, base: str, destination: Path, worktrees: Path) -> None:
    """Build documentation from one Git ref in an isolated temporary worktree."""
    checkout = worktrees / re.sub(r"[^a-zA-Z0-9.-]", "-", ref)
    run("git", "worktree", "add", "--detach", str(checkout), ref, cwd=ROOT)
    try:
        patch_historical_base(checkout / "docs" / "astro.config.mjs", base)
        environment = os.environ.copy()
        environment.update({"LYRA_DOCS_BASE": base, "LYRA_DOCS_REF": ref})
        run("npm", "ci", cwd=checkout / "docs", env=environment)
        run("npm", "run", "build", cwd=checkout / "docs", env=environment)
        shutil.copytree(checkout / "docs" / "dist", destination)
    finally:
        run("git", "worktree", "remove", "--force", str(checkout), cwd=ROOT)


def version_manifest(releases: list[Release]) -> list[dict[str, str]]:
    """Build selector metadata for latest and versioned sites.

    Returns:
        Ordered version labels, identifiers, and URL bases for the selector.

    Raises:
        VersionedSiteError: If no supported product release is available.
    """
    if not releases:
        message = "No supported Lyra product release tags exist"
        raise VersionedSiteError(message)
    return [
        {"label": "Latest", "version": "main", "base": "/lyra/latest"},
        *[
            {
                "label": "Current release" if release == releases[0] else "Version",
                "version": release.label,
                "base": release.base,
            }
            for release in releases
        ],
    ]


def selector_markup(manifest: list[dict[str, str]], current_base: str) -> str:
    """Render the documentation version selector for one site tree.

    Returns:
        Self-contained HTML, CSS, and JavaScript for selecting a version.
    """
    options = []
    for item in manifest:
        selected = " selected" if item["base"] == current_base else ""
        options.append(
            f'<option value="{html.escape(item["base"])}"{selected}>'
            f"{html.escape(item['label'])} ({html.escape(item['version'])})</option>"
        )
    notice = (
        "<span>May include unreleased changes</span>"
        if current_base == "/lyra/latest"
        else ""
    )
    return (
        '<aside class="lyra-version-selector" aria-label="Documentation version">'
        f"{notice}"
        '<label for="lyra-docs-version">Documentation</label>'
        f'<select id="lyra-docs-version">{"".join(options)}</select>'
        "</aside>"
        "<style>"
        ".lyra-version-selector{align-items:center;background:var(--sl-color-bg-nav);"
        "border-bottom:1px solid var(--sl-color-hairline);display:flex;gap:.5rem;"
        "justify-content:flex-end;padding:.45rem 1rem;position:relative;z-index:20}"
        ".lyra-version-selector label{font-size:.8rem;font-weight:600}"
        ".lyra-version-selector select{background:var(--sl-color-bg);border:1px solid "
        "var(--sl-color-gray-5);border-radius:.25rem;color:var(--sl-color-text);"
        "padding:.2rem .4rem}"
        "</style>"
        "<script>document.getElementById('lyra-docs-version').addEventListener('change',"
        "function(){window.location.assign(this.value + '/');});</script>"
    )


def inject_selector(
    site: Path, manifest: list[dict[str, str]], current_base: str
) -> None:
    """Inject the version selector immediately inside every HTML body.

    Raises:
        VersionedSiteError: If a generated HTML page has no body element.
    """
    markup = selector_markup(manifest, current_base)
    for page in site.rglob("*.html"):
        content = page.read_text(encoding="utf-8")
        if "lyra-version-selector" in content:
            continue
        if "<body" not in content:
            message = f"HTML page has no body: {page}"
            raise VersionedSiteError(message)
        content = re.sub(r"(<body[^>]*>)", rf"\1{markup}", content, count=1)
        page.write_text(content, encoding="utf-8")


def cached_build(
    release: Release, destination: Path, worktrees: Path, cache_dir: Path | None
) -> None:
    """Reuse a complete raw release tree or build it from its immutable source."""
    if cache_dir is None:
        build_ref(release.tag, release.base, destination, worktrees)
        return
    sha = run(
        "git", "rev-parse", f"{release.tag}^{{commit}}", cwd=ROOT, capture_output=True
    ).stdout.strip()
    fingerprint = hashlib.sha256(
        (ARCHIVE_RECIPE_VERSION + sha + release.tag + release.base).encode()
    ).hexdigest()
    entry = cache_dir / fingerprint
    tree = entry / "site"
    try:
        if (tree / "index.html").is_file() and (entry / "complete").read_text(
            encoding="utf-8"
        ) == json.dumps(
            sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file())
        ):
            shutil.copytree(tree, destination)
            sys.stderr.write(f"Archive cache hit: {release.label}\n")
            return
    except OSError:
        if destination.exists():
            shutil.rmtree(destination)

    sys.stderr.write(f"Building archive: {release.label}\n")
    build_ref(release.tag, release.base, destination, worktrees)
    try:
        if entry.exists():
            shutil.rmtree(entry)
        shutil.copytree(destination, tree)
        (entry / "complete").write_text(
            json.dumps(
                sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file())
            ),
            encoding="utf-8",
        )
    except OSError:
        # Cache storage is optional; the completed build remains usable.
        return


def assemble(
    output: Path,
    latest_ref: str,
    latest_build: Path | None = None,
    cache_dir: Path | None = None,
) -> None:
    """Assemble latest and versioned builds into one site tree."""
    releases = discover_releases()
    manifest = version_manifest(releases)
    with tempfile.TemporaryDirectory(prefix="lyra-docs-") as temporary:
        temporary_path = Path(temporary)
        worktrees = temporary_path / "worktrees"
        builds = temporary_path / "builds"
        worktrees.mkdir()
        builds.mkdir()

        if latest_build is None:
            latest_build = builds / "latest"
            build_ref(latest_ref, "/lyra/latest", latest_build, worktrees)
        release_builds: dict[Release, Path] = {}
        for release in releases:
            destination = builds / release.label
            cached_build(release, destination, worktrees, cache_dir)
            release_builds[release] = destination

        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)
        shutil.copytree(latest_build, output / "latest")
        for release, build in release_builds.items():
            shutil.copytree(build, output / "versions" / release.label)

    inject_selector(output / "latest", manifest, "/lyra/latest")
    for release in releases:
        inject_selector(output / "versions" / release.label, manifest, release.base)
    target = f"{releases[0].base}/"
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={target}">'
        f'<link rel="canonical" href="https://rodolfofigueroa.github.io{target}">'
        "<title>Lyra documentation</title></head><body>"
        f'<a href="{target}">Open the current release documentation</a>'
        "</body></html>",
        encoding="utf-8",
    )
    (output / "versions.json").write_text(
        f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the versioned-site command-line parser.

    Returns:
        The parser for output-path and latest-ref options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--latest-ref", default="main")
    parser.add_argument("--latest-build", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    return parser


def main() -> None:
    """Build the versioned documentation tree from command-line arguments."""
    arguments = build_parser().parse_args()
    if arguments.inventory:
        for release in discover_releases():
            sha = run(
                "git",
                "rev-parse",
                f"{release.tag}^{{commit}}",
                cwd=ROOT,
                capture_output=True,
            ).stdout.strip()
            sys.stdout.write(f"{release.tag} {sha}\n")
        return
    if arguments.output is None:
        build_parser().error("--output is required when assembling documentation")
    assemble(
        arguments.output.resolve(),
        arguments.latest_ref,
        arguments.latest_build,
        arguments.cache_dir,
    )


if __name__ == "__main__":
    main()
