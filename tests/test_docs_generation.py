import json
import re
import shutil
from pathlib import Path

import pytest

from docs.scripts import generate_docs as docs
from docs.scripts.mcp_tools import advertised_tools


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a small site whose contracts and page URLs identify their generation."""
    root = tmp_path / "docs"
    authored = root / docs.CONTENT / "reference/index.md"
    docs.write_text(authored, "---\ntitle: Guide\ndescription: Test\n---\nGuide")
    docs.write_json(
        root / "navigation.json",
        [{"items": ["reference/index", "reference/generated"]}],
    )
    docs.write_text(root / "revision", "first")

    def contracts(generated: Path, public: Path) -> None:
        revision = (root / "revision").read_text().strip()
        docs.write_text(
            generated / f"{revision}.md",
            f"---\ntitle: {revision}\ndescription: Test\n---\n{revision}",
        )
        for name in ("openapi.json", "config.schema.json", "mcp-tools.json"):
            docs.write_json(public / name, {"revision": revision})

    monkeypatch.setattr(docs, "generate_contracts", contracts)
    docs.main(root)
    return root


def published(site: Path) -> dict[Path, bytes]:
    generated = site / docs.CONTENT / docs.GENERATED
    files = [
        *generated.rglob("*.md"),
        *(site / "public" / name for name in docs.PUBLIC_FILES),
    ]
    return {path.relative_to(site): path.read_bytes() for path in files}


def test_repeated_generation_reconciles_only_owned_outputs(site: Path) -> None:
    generated = site / docs.CONTENT / docs.GENERATED
    retained = [
        site / docs.CONTENT / "reference/index.md",
        site / "public" / "api/lyra/llms.txt",
        site / "public" / "examples/example.json",
    ]
    for path in retained[1:]:
        docs.write_text(path, "fixture")
    saved = {path: path.read_bytes() for path in retained}
    docs.write_text(generated / "nested/stale.md", "stale")
    (generated / "linked").symlink_to(retained[-1].parent, target_is_directory=True)
    docs.write_text(site / "revision", "second")
    docs.main(site)

    assert list(generated.iterdir()) == [generated / "second.md"]
    assert {path: path.read_bytes() for path in retained} == saved
    for name in ("openapi.json", "config.schema.json", "mcp-tools.json"):
        assert json.loads((site / "public" / name).read_bytes()) == {
            "revision": "second"
        }
    for name in ("llms.txt", "llms-full.txt"):
        rendered = (site / "public" / name).read_text()
        assert "/reference/generated/second/" in rendered
        assert "/reference/generated/first/" not in rendered
        assert "/reference/)" in rendered
    saved_outputs = published(site)
    docs.main(site)
    assert published(site) == saved_outputs


@pytest.mark.parametrize(
    ("slug", "error"),
    [
        ("missing", "Navigation page does not exist"),
        ("../outside", "Unsafe documentation path"),
        ("/outside", "Unsafe documentation path"),
    ],
)
def test_navigation_failure_preserves_last_generation(
    site: Path, slug: str, error: str
) -> None:
    before = published(site)
    docs.write_text(site / "revision", "second")
    docs.write_json(site / "navigation.json", [{"items": [slug]}])
    with pytest.raises(docs.DocumentationGenerationError, match=error):
        docs.main(site)
    assert published(site) == before


@pytest.mark.parametrize("absolute", [False, True])
def test_rejects_escaping_output_before_rendering(
    site: Path, monkeypatch: pytest.MonkeyPatch, *, absolute: bool
) -> None:
    outside = site.parent / "outside"
    unsafe = outside if absolute else Path("../../../../../outside")
    before = published(site)
    with monkeypatch.context() as patch:
        patch.setattr(docs, "GENERATED", unsafe)
        with pytest.raises(docs.DocumentationGenerationError, match="Unsafe"):
            docs.main(site)
    assert not outside.exists()
    assert published(site) == before


def test_rejects_unowned_staged_public_file(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = site / "public" / "favicon.svg"
    docs.write_text(asset, "authored")
    before = published(site)
    generate = docs.generate_contracts

    def extra_file(generated: Path, public: Path) -> None:
        generate(generated, public)
        docs.write_text(public / asset.name, "unowned")

    monkeypatch.setattr(docs, "generate_contracts", extra_file)
    docs.write_text(site / "revision", "second")
    with pytest.raises(docs.DocumentationGenerationError, match="inventory"):
        docs.main(site)
    assert asset.read_text().strip() == "authored"
    assert published(site) == before


@pytest.mark.parametrize(
    "relative",
    [
        docs.CONTENT / "reference",
        docs.CONTENT / docs.GENERATED,
        Path("public"),
        Path("public/llms-full.txt"),
    ],
)
def test_rejects_symlinked_output_paths(site: Path, relative: Path) -> None:
    before = published(site)
    target = site / relative
    outside = site.parent / "external"
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    docs.write_text(site / "revision", "second")
    with pytest.raises(docs.DocumentationGenerationError, match="Symlink"):
        docs.main(site)
    assert target.is_symlink()
    assert published(site) == before


def test_folded_frontmatter_and_markdown_body(tmp_path: Path) -> None:
    page = tmp_path / "folded.md"
    body = "    first()\n    second()\n\n---\nMore\n"
    page.write_text(
        f"---\ntitle: 'A: guide'\ndescription: >-\n  first\n  second\n---\n{body}"
    )
    assert docs.read_frontmatter(page) == ("A: guide", "first second", body)


@pytest.mark.parametrize(
    "header",
    [
        "title: missing delimiters",
        "---\ntitle: unclosed",
        "---\ntitle: [\n---",
        "---\n- item\n---",
        "---\ntitle: Guide\n---",
        "---\ntitle: 12\ndescription: Test\n---",
        "---\ntitle: Guide\ndescription: ' '\n---",
        "---\ntitle: Guide\ndescription: 2026-02-30\n---",
    ],
)
def test_invalid_frontmatter_preserves_generation(site: Path, header: str) -> None:
    before = published(site)
    page = site / docs.CONTENT / "reference/index.md"
    docs.write_text(page, header + "\nBody")
    docs.write_text(site / "revision", "second")
    with pytest.raises(docs.DocumentationGenerationError) as error:
        docs.main(site)
    assert str(page) in str(error.value)
    assert published(site) == before


def test_publication_error_is_repaired_by_rerun(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.replace
    failed = False

    def replace(source: Path, target: Path) -> Path:
        nonlocal failed
        if not failed and target == site / "public" / "llms.txt":
            failed = True
            message = "injected write error"
            raise OSError(message)
        return original(source, target)

    docs.write_text(site / "revision", "second")
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="injected write error"):
        docs.main(site)
    docs.main(site)

    clean = site.parent / "clean"
    shutil.copytree(
        site / docs.CONTENT / "reference",
        clean / docs.CONTENT / "reference",
        ignore=shutil.ignore_patterns("generated"),
    )
    shutil.copyfile(site / "navigation.json", clean / "navigation.json")
    docs.main(clean)
    assert published(site) == published(clean)


def test_generated_mcp_schemas_and_annotations_match_advertised_tools(
    tmp_path: Path,
) -> None:
    docs.generate_contracts(tmp_path / "reference", tmp_path / "public")
    generated = json.loads((tmp_path / "public/mcp-tools.json").read_text())
    advertised = advertised_tools()
    assert advertised
    assert {tool["name"] for tool in generated["tools"]} == set(advertised)
    for tool in generated["tools"]:
        live = advertised[tool["name"]].model_dump(mode="json", exclude_none=True)
        for field in ("inputSchema", "outputSchema", "annotations"):
            assert tool[field] == live[field]

    # Readers of the Markdown schemas must get the same wire contracts as JSON
    # consumers, including both wrapped inspection and direct object responses.
    markdown = (tmp_path / "reference/mcp.md").read_text()
    embedded = [
        json.loads(source)
        for source in re.findall(r"```json\n(.*?)\n```", markdown, re.DOTALL)
    ]
    expected = [
        tool[field]
        for tool in generated["tools"]
        for field in ("inputSchema", "outputSchema")
    ]
    assert embedded == expected
