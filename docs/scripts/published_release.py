"""Select the highest stable published Lyra version and resolve its current tag."""

from __future__ import annotations

import json
import os
import sys

from build_scripts.release import COMMIT_PATTERN
from docs.scripts.versioned_site import ROOT, VersionedSiteError, parse_release, run


def published_release(repository: str) -> tuple[str, str]:
    """Return the selected tag and commit, or two empty strings before publication.

    Raises:
        VersionedSiteError: If GitHub returns an invalid commit identity.
    """
    endpoint = f"repos/{repository}"
    pages = run(
        "gh",
        "api",
        "--paginate",
        "--slurp",
        f"{endpoint}/releases?per_page=100",
        cwd=ROOT,
        capture_output=True,
    )
    releases = (
        parsed
        for page in json.loads(pages.stdout)
        for release in page
        if release["draft"] is False and release["prerelease"] is False
        if (parsed := parse_release(release["tag_name"])) is not None
    )
    selected = max(releases, default=None)
    if selected is None:
        return "", ""
    sha = run(
        "gh",
        "api",
        f"{endpoint}/commits/tags/{selected.tag}",
        "--jq",
        ".sha",
        cwd=ROOT,
        capture_output=True,
    ).stdout.strip()
    if COMMIT_PATTERN.fullmatch(sha) is None:
        message = f"Invalid source commit for {selected.tag}: {sha!r}"
        raise VersionedSiteError(message)
    return selected.tag, sha


if __name__ == "__main__":
    tag, sha = published_release(os.environ["GITHUB_REPOSITORY"])
    sys.stdout.write(f"tag={tag}\nsha={sha}\n")
