"""Build one GitHub Pages tree from main and the current published product release."""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- invokes Git/npm
import tempfile
from dataclasses import dataclass
from pathlib import Path

from docs.scripts.check_site import broken_links

ROOT = Path(__file__).resolve().parents[2]
TAG_PATTERN = re.compile(r"^lyra-v(?P<version>\d+\.\d+\.\d+)$")


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
    return Release(version=version, tag=tag)


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


def build_ref(ref: str, base: str, destination: Path, worktrees: Path) -> None:
    """Build documentation from one Git ref in an isolated temporary worktree."""
    checkout = worktrees / re.sub(r"[^a-zA-Z0-9.-]", "-", ref)
    run("git", "worktree", "add", "--detach", str(checkout), ref, cwd=ROOT)
    try:
        environment = os.environ.copy()
        environment.update({"LYRA_DOCS_BASE": base, "LYRA_DOCS_REF": ref})
        run("npm", "ci", cwd=checkout / "docs", env=environment)
        run("npm", "run", "build", cwd=checkout / "docs", env=environment)
        shutil.copytree(checkout / "docs" / "dist", destination)
    finally:
        run("git", "worktree", "remove", "--force", str(checkout), cwd=ROOT)


def selector_markup(release: Release, current_base: str, *, unreleased: bool) -> str:
    """Render links to the two documentation targets.

    Returns:
        Navigation markup with an optional development notice.
    """
    links = []
    for base, label in [
        (release.base, f"Released Documentation ({release.label})"),
        ("/lyra/latest", "Latest Development Documentation"),
    ]:
        current = ' aria-current="page"' if base == current_base else ""
        links.append(f'<a href="{base}/"{current}>{html.escape(label)}</a>')
    notice = "<span>Contains unreleased changes</span>" if unreleased else ""
    return (
        '<nav class="lyra-version-selector" aria-label="Documentation version">'
        + " | ".join(links)
        + notice
        + "</nav>"
        "<style>.lyra-version-selector{display:flex;gap:1rem;flex-wrap:wrap;"
        "padding:.75rem 1rem;background:var(--sl-color-bg-nav)}</style>"
    )


def inject_selector(
    site: Path, release: Release, current_base: str, *, unreleased: bool = False
) -> None:
    """Inject the version selector immediately inside every HTML body.

    Raises:
        VersionedSiteError: If a generated HTML page has no body element.
    """
    markup = selector_markup(release, current_base, unreleased=unreleased)
    for page in site.rglob("*.html"):
        content = page.read_text(encoding="utf-8")
        if "lyra-version-selector" in content:
            continue
        if "<body" not in content:
            message = f"HTML page has no body: {page}"
            raise VersionedSiteError(message)
        content = re.sub(r"(<body[^>]*>)", rf"\1{markup}", content, count=1)
        page.write_text(content, encoding="utf-8")


def assemble(
    output: Path,
    latest_sha: str,
    release_tag: str,
    release_sha: str,
    latest_build: Path | None = None,
) -> None:
    """Build and validate both targets before replacing the assembled site.

    Raises:
        VersionedSiteError: If source identities or a supplied artifact are invalid.
    """
    release = parse_release(release_tag)
    if release is None or any(
        re.fullmatch(r"[0-9a-f]{40}", sha) is None for sha in (latest_sha, release_sha)
    ):
        message = "A product release tag and full commit SHAs are required"
        raise VersionedSiteError(message)
    if latest_build is not None:
        for filename, expected in [
            ("source-sha.txt", latest_sha),
            ("source-base.txt", "/lyra/latest"),
        ]:
            if (latest_build / filename).read_text().strip() != expected:
                message = "Latest artifact identity mismatch"
                raise VersionedSiteError(message)
    with tempfile.TemporaryDirectory(prefix="lyra-docs-") as temporary:
        staging = Path(temporary)
        worktrees = staging / "worktrees"
        worktrees.mkdir()
        site = staging / "site"
        site.mkdir()
        if latest_build is None:
            build_ref(latest_sha, "/lyra/latest", site / "latest", worktrees)
        else:
            shutil.copytree(latest_build, site / "latest")
        for name in ("source-sha.txt", "source-base.txt"):
            (site / "latest" / name).unlink(missing_ok=True)
        released = site / "versions" / release.label
        released.parent.mkdir()
        build_ref(release_sha, release.base, released, worktrees)
        inject_selector(
            site / "latest",
            release,
            "/lyra/latest",
            unreleased=latest_sha != release_sha,
        )
        inject_selector(released, release, release.base)
        target = f"{release.base}/"
        (site / "index.html").write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0;url={target}">'
            "<title>Lyra documentation</title></head><body>"
            f'<a href="{target}">Open the current release documentation</a>'
            "</body></html>",
            encoding="utf-8",
        )
        failures = broken_links(site, "/lyra")
        if failures:
            raise VersionedSiteError("\n".join(failures))
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(site, output)


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for explicit documentation build identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--latest-sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--latest-build", type=Path)
    return parser


def main() -> None:
    """Assemble documentation using the supplied immutable source identities."""
    args = build_parser().parse_args()
    assemble(
        args.output.resolve(),
        args.latest_sha,
        args.release_tag,
        args.release_sha,
        args.latest_build,
    )


if __name__ == "__main__":
    main()
