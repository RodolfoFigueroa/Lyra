from __future__ import annotations

import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from docs.scripts import generate_docs
from docs.scripts.generate_docs import CONTENT_DIR

if TYPE_CHECKING:
    from collections.abc import Generator

    import pytest


ROOT = Path(__file__).parents[1]


CITY_PLUGIN_DIR = ROOT / "examples/city-plugin"


def published_python(relative: str, filename: str) -> str:
    sources = published_python_files(relative)
    assert filename in sources, f"No excerpts found for {filename} in {relative}"
    return sources[filename]


def published_python_files(relative: str) -> dict[str, str]:
    """Assemble the executable files identified by code-fence titles."""
    content = generate_docs.normalize_mdx((CONTENT_DIR / relative).read_text())
    blocks = re.findall(
        r"^[ \t]*```python([^\n]*)\n(.*?)^[ \t]*```",
        content,
        re.MULTILINE | re.DOTALL,
    )
    sources: dict[str, list[str]] = {}
    for metadata, source in blocks:
        title = re.search(r'title="([^"]+)"', metadata)
        if title is not None:
            sources.setdefault(title[1], []).append(dedent(source).strip())
    return {name: "\n\n".join(parts) for name, parts in sources.items()}


@contextmanager
def city_tutorial_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chapter: int = 8
) -> Generator[tuple[dict[str, str], pytest.MonkeyPatch], None, None]:
    """Expose the reader's source package without installing a distribution.

    Yields:
        Assembled source files and the scoped module patcher.
    """
    pages = (
        "first-metric.mdx",
        "select-streets.mdx",
        "parameters.mdx",
        "dependencies.mdx",
        "blocks.mdx",
        "canonical.md",
        "testing.md",
        "package.md",
    )
    shutil.copytree(
        CITY_PLUGIN_DIR / "city_example",
        tmp_path / "city_example",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copyfile(CITY_PLUGIN_DIR / "pyproject.toml", tmp_path / "pyproject.toml")
    sources: dict[str, str] = {}
    for page in pages[:chapter]:
        for filename, source in published_python_files(
            f"plugins/tutorial/{page}"
        ).items():
            # Later complete definitions replace earlier ones through normal
            # Python rebinding, matching the tutorial's incremental edits.
            sources[filename] = sources.get(filename, "") + "\n\n" + source
    for filename, source in sources.items():
        destination = tmp_path / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)

    root_modules = {Path(filename).stem for filename in sources if "/" not in filename}

    def belongs_to_workspace(name: str) -> bool:
        return (
            name == "city_example"
            or name.startswith("city_example.")
            or name in root_modules
        )

    # Use the copied source package, including its actual public export, rather
    # than any city modules cached by other tests. No package install is needed.
    with monkeypatch.context() as workspace:
        workspace.syspath_prepend(str(tmp_path))
        for name in tuple(sys.modules):
            if belongs_to_workspace(name):
                workspace.delitem(sys.modules, name)
        try:
            yield sources, workspace
        finally:
            for name in tuple(sys.modules):
                if belongs_to_workspace(name):
                    del sys.modules[name]
