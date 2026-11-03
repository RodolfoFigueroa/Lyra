"""Assemble consecutive file excerpts, including code nested in MDX steps."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import tomllib
from contextlib import contextmanager
from copy import deepcopy
from importlib import import_module
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
from jsonschema import validate
from lyra.sdk import (
    GraphExecutionError,
    LocalRunner,
    RunContext,
    UnresolvedLocationIDsError,
    plugin_loader,
)
from lyra.sdk.config import WorkerConfig
from lyra.sdk.plugin_cli import build_parsers as build_plugin_parsers
from lyra.sdk.plugin_cli import main as plugin_main
from lyra.sdk.plugin_cli import render_manifest
from typing_extensions import override

from docs.scripts import generate_docs, versioned_site
from docs.scripts.check_site import api_reference_failures, broken_links
from docs.scripts.generate_docs import (
    CONTENT_DIR,
    ENV_FIELDS,
    config_rows,
    create_openapi_app,
    navigation_pages,
    schema_fields,
)
from docs.scripts.versioned_site import (
    Release,
    inject_selector,
    parse_release,
)
from lyra_app.config import LyraConfig
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME
from tests.plugin_helpers import plugin_config
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType

    import geopandas as gpd
    from lyra.sdk.location_contracts import LocationContract

ROOT = Path(__file__).parents[1]
CITY_PLUGIN_DIR = ROOT / "examples/city-plugin"


@pytest.fixture
def installed_smoke_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose the source manifest through installed-distribution boundaries."""
    config = plugin_config(SMOKE_PLUGIN_DIR)
    paths = {config.distribution: config.manifest_path}
    monkeypatch.setattr(plugin_loader, "installed_manifest_path", paths.__getitem__)


@pytest.fixture
def installed_city_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply source metadata for the city plugin without package installation."""
    config = plugin_config(CITY_PLUGIN_DIR)
    paths = {config.distribution: config.manifest_path}
    monkeypatch.setattr(plugin_loader, "installed_manifest_path", paths.__getitem__)


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


def load_documented_module(
    path: Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Import an assembled example with normal module and decorator semantics."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def assert_excerpt_matches(excerpt: object, complete: object) -> None:
    """Check every displayed value against the complete response beside it."""
    if isinstance(excerpt, dict):
        assert isinstance(complete, dict)
        for key, value in excerpt.items():
            assert_excerpt_matches(value, complete[key])
    elif isinstance(excerpt, list):
        assert isinstance(complete, list)
        assert len(excerpt) == len(complete)
        for value, expected in zip(excerpt, complete, strict=True):
            assert_excerpt_matches(value, expected)
    else:
        assert excerpt == complete


def test_editorial_plans_cover_all_published_guides() -> None:
    plans = json.loads((ROOT / "docs" / "content-plan.json").read_text())["pages"]
    for page in navigation_pages():
        content = page.read_text()
        plan_key = re.search(r"^contentPlan: (.+)$", content, re.MULTILINE)
        content_type = re.search(r"^contentType: (.+)$", content, re.MULTILINE)
        assert plan_key is not None, page
        assert content_type is not None, page
        plan = plans[plan_key[1]]
        assert plan["contentType"] == content_type[1]
        assert all(
            plan[field] for field in ("overview", "goal", "audience", "contentPlan")
        )
        assert isinstance(plan["openQuestions"], list)


def test_mdx_exports_preserve_nested_examples_without_component_markup() -> None:
    body = """import { Steps } from '@astrojs/starlight/components';

<Steps>

1. <span id="example"></span>

   Read the package metadata:

   <CodeExample path="examples/lyra-plugin/pyproject.toml" lang="toml" />

</Steps>
"""
    markdown = generate_docs.normalize_mdx(body)
    assert "import { Steps }" not in markdown
    assert "<Steps>" not in markdown
    assert "<span" not in markdown
    assert "<CodeExample" not in markdown
    assert '   ```toml\n   [project]\n   name = "lyra-smoke-plugin"' in markdown
    assert markdown.rstrip().endswith("```")


def test_navigation_is_complete_unique_and_resolvable() -> None:
    navigation = json.loads((ROOT / "docs" / "navigation.json").read_text())
    authored_slugs = [
        slug
        for group in navigation
        for slug in generate_docs.navigation_slugs(group["items"])
        if slug not in {"reference/generated", "api/lyra"}
    ]

    assert len(authored_slugs) == len(set(authored_slugs))
    assert all(path.is_relative_to(CONTENT_DIR) for path in navigation_pages())
    authored_paths = {
        path
        for path in CONTENT_DIR.rglob("*")
        if path.suffix in {".md", ".mdx"}
        and not path.is_relative_to(generate_docs.GENERATED_DIR)
    }
    assert authored_paths == {
        path
        for path in navigation_pages()
        if not path.is_relative_to(generate_docs.GENERATED_DIR)
    }


def test_link_checker_checks_local_and_cross_page_anchors(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<a href="#intro">Intro</a><h2 id="intro">Intro</h2>'
        '<a href="guide/#valid%20anchor">Guide</a>'
        '<a href="guide/#missing">Missing anchor</a>'
        '<a href="absent/">Missing page</a>'
        '<a href="https://example.com/#outside">External</a>'
    )
    guide = tmp_path / "guide"
    guide.mkdir()
    (guide / "index.html").write_text('<h2 id="valid anchor">Valid</h2>')

    assert broken_links(tmp_path, "/Lyra/latest") == [
        "index.html -> absent/",
        "index.html -> guide/#missing",
    ]


@pytest.mark.parametrize("path_kind", ["relative", "symlink"])
def test_link_checker_accepts_site_path_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_kind: str
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<a href="missing/">Missing</a>')
    if path_kind == "relative":
        monkeypatch.chdir(tmp_path)
        supplied_site = Path("site")
    else:
        supplied_site = tmp_path / "alias"
        supplied_site.symlink_to(site, target_is_directory=True)

    assert broken_links(supplied_site, "/Lyra/latest") == ["index.html -> missing/"]


def test_legacy_python_cleanup_preserves_other_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    legacy = tmp_path / "python"
    legacy.mkdir()
    (legacy / "old.md").write_text("obsolete")
    other = tmp_path / "configuration.md"
    other.write_text("keep")

    generate_docs.remove_legacy_python_reference()
    generate_docs.remove_legacy_python_reference()

    assert not legacy.exists()
    assert other.read_text() == "keep"


def test_llm_exports_link_to_versioned_python_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "PUBLIC_DIR", tmp_path)
    monkeypatch.setattr(generate_docs, "navigation_pages", list)
    monkeypatch.setenv("LYRA_DOCS_BASE", "/Lyra/versions/test/")

    generate_docs.generate_llm_files()

    for name in ("llms.txt", "llms-full.txt"):
        content = (tmp_path / name).read_text()
        assert "/Lyra/versions/test/api/lyra/llms.txt" in content
        assert "reference/generated/python" not in content


def test_portable_markdown_resolves_links_and_preserves_code() -> None:
    body = """[Relative](../target/?q=value#part) and [Local](#part).

[Reference][shared] and ![Image](./image.png).

[shared]: <../reference/> "Reference title"

| Link | Code |
| --- | --- |
| [Nested **label**](../nested_(page)/) | `[Code](../unchanged/)` |

- [List item](../list/)

```markdown
[Code](../unchanged/)
```

[External](https://example.org/path) and [Email](mailto:reader@example.org).
"""
    page_url = "https://example.org/Lyra/versions/test/use/page/"
    rendered = generate_docs.portable_markdown(body, page_url)
    assert (
        "(https://example.org/Lyra/versions/test/use/target/?q=value#part)" in rendered
    )
    assert f"({page_url}#part)" in rendered
    assert (
        '(https://example.org/Lyra/versions/test/use/reference/ "Reference title")'
        in rendered
    )
    assert f"![Image]({page_url}image.png)" in rendered
    assert "(<https://example.org/Lyra/versions/test/use/nested_(page)/>)" in rendered
    assert "(https://example.org/Lyra/versions/test/use/list/)" in rendered
    assert "`[Code](../unchanged/)`" in rendered
    assert "```markdown\n[Code](../unchanged/)\n```" in rendered
    assert "(https://example.org/path)" in rendered
    assert "(mailto:reader@example.org)" in rendered
    assert "[shared]" not in rendered


def test_llm_export_keeps_each_pages_link_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_dir = tmp_path / "content"
    pages = [content_dir / section / "index.md" for section in ("first", "second")]
    for page in pages:
        page.parent.mkdir(parents=True)
        page.write_text(
            "---\ntitle: Test\ndescription: Test page\n---\n"
            "[Target][shared]\n\n[shared]: ./target/\n"
        )
    monkeypatch.setattr(generate_docs, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(generate_docs, "PUBLIC_DIR", tmp_path)
    monkeypatch.setattr(generate_docs, "navigation_pages", lambda: pages)
    monkeypatch.setenv("LYRA_DOCS_BASE", "/Lyra/versions/test/")
    generate_docs.generate_llm_files()
    rendered = (tmp_path / "llms-full.txt").read_text()
    for section in ("first", "second"):
        url = f"{generate_docs.SITE}/Lyra/versions/test/{section}/"
        assert f"[Test]({url})" in rendered
        assert f"[Target]({url}target/)" in rendered


def test_generated_plugin_help_includes_each_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    generate_docs.generate_cli_reference()
    rendered = (tmp_path / "cli.md").read_text()
    help_blocks = re.findall(r"```text\n(.*?)\n```", rendered, re.DOTALL)
    parsers = build_plugin_parsers()
    assert len(parsers) == 5
    assert all(parser.format_help().rstrip() in help_blocks for parser in parsers)


def test_api_reference_checker_validates_artifacts_and_anchors(tmp_path: Path) -> None:
    api = tmp_path / "api" / "lyra"
    api.mkdir(parents=True)
    for name in ("objects.inv", "llms.txt"):
        (api / name).write_text("artifact")
    (api / "index.html").write_text('<h2 id="lyra.Example">Example</h2>')
    for suffix in (".md", ".md.txt"):
        (tmp_path / f"api/lyra{suffix}").write_text("# lyra")
    symbols = [
        {"page": "api/lyra", "anchor": "", "kind": "module"},
        {"page": "api/lyra", "anchor": "lyra.Example", "kind": "class"},
    ]
    index = api / "symbols.json"
    index.write_text(json.dumps({"symbols": symbols}))
    assert api_reference_failures(tmp_path) == []

    (api / "index.html").write_text("<h2>Missing anchor</h2>")
    assert api_reference_failures(tmp_path) == [
        "Missing API symbol anchor: api/lyra#lyra.Example"
    ]
    (api / "objects.inv").unlink()
    assert "Missing or empty API artifact: api/lyra/objects.inv" in (
        api_reference_failures(tmp_path)
    )
    index.write_text("invalid json")
    assert "Invalid API symbol index" in api_reference_failures(tmp_path)


def test_api_reference_checker_rejects_missing_and_empty_index(tmp_path: Path) -> None:
    assert "Missing or empty API artifact: api/lyra/symbols.json" in (
        api_reference_failures(tmp_path)
    )
    api = tmp_path / "api" / "lyra"
    api.mkdir(parents=True)
    (api / "symbols.json").write_text('{"symbols": []}')
    assert "API symbol index must contain symbols" in api_reference_failures(tmp_path)


def test_generated_openapi_has_explicit_authentication_boundaries() -> None:
    schema = create_openapi_app().openapi()
    schemes = schema["components"]["securitySchemes"]

    assert set(schemes) == {"AdminBearer", "AgentBearer"}
    assert schema["paths"]["/live"]["get"].get("security", []) == []
    assert schema["paths"]["/jobs"]["post"]["security"] == [{"AgentBearer": []}]
    assert schema["paths"]["/admin/jobs"]["get"]["security"] == [{"AdminBearer": []}]
    assert all(
        operation.get("tags")
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    )


def test_every_leaf_config_field_is_documentable() -> None:
    schema = LyraConfig.model_json_schema()
    rows = config_rows(schema, root=schema)
    paths = {row.path for row in rows}

    assert rows
    assert all(row.description for row in rows)
    assert "redis.url" in paths
    assert {
        "plugins.installed[].distribution",
        "plugins.installed[].enabled",
        "plugins.installed[].routing",
        "plugins.installed[].manifest_path",
    } <= paths


def test_environment_example_covers_generated_environment_reference() -> None:
    example = (ROOT / ".env.example").read_text()

    assert all(variable in example for variable in ENV_FIELDS)


def test_canonical_plugin_example_is_the_only_documented_example() -> None:
    example = ROOT / "examples" / "lyra-plugin"

    assert (example / "lyra.plugin.json").is_file()
    assert (example / "smoke_plugin" / "metrics.py").is_file()
    assert (example / "smoke_plugin" / "plugin.py").is_file()
    assert not (
        ROOT / "tests" / "fixtures" / "plugins" / "smoke_plugin" / "lyra.plugin.json"
    ).exists()


def test_release_parser_accepts_only_product_tags() -> None:
    assert parse_release("lyra-v0.8.0") == Release((0, 8, 0), "lyra-v0.8.0")
    for tag in ("lyra-app-v0.7.0", "lyra-sdk-v9.0.0", "lyra-v1.0", "lyra-v1.0.0-rc1"):
        assert parse_release(tag) is None


def test_version_links_are_idempotent(tmp_path: Path) -> None:
    release = Release((0, 8, 0), "lyra-v0.8.0")
    page = tmp_path / "index.html"
    page.write_text("<html><body>Docs</body></html>")
    for _ in range(2):
        inject_selector(tmp_path, release, "/Lyra/latest", unreleased=True)
    rendered = page.read_text()
    assert rendered.count('class="lyra-version-selector"') == 1
    assert 'href="/Lyra/latest/" aria-current="page"' in rendered
    assert 'href="/Lyra/versions/0.8.0/"' in rendered
    assert "<script>" not in rendered


@pytest.mark.parametrize("development", [False, True])
def test_version_dropdown_identifies_current_target(*, development: bool) -> None:
    release = Release((0, 26, 0), "lyra-v0.26.0")
    base = "/Lyra/latest" if development else release.base
    rendered = versioned_site.selector_markup(release, base, unreleased=development)
    assert "<details><summary>" in rendered
    assert "</summary>" in rendered
    assert f'href="{base}/" aria-current="page"' in rendered
    assert rendered.count(' aria-current="page"') == 1
    assert "position:fixed" in rendered
    assert "bottom:1rem" in rendered


@pytest.mark.parametrize(
    "relative",
    ["use/mcp.md", "reference/mcp-payloads.md"],
)
def test_mcp_workflow_examples_match_serialization_schemas(relative: str) -> None:
    page = (CONTENT_DIR / relative).read_text()
    examples = []
    for match in re.finditer(
        r"```json\n(?P<inline>.*?)\n```"
        r"|\]\(\.\./\.\./(?P<download>examples/[^)]+\.json)\)",
        page,
        re.DOTALL,
    ):
        if match["inline"] is not None:
            examples.append(json.loads(match["inline"]))
        else:
            path = (generate_docs.PUBLIC_DIR / match["download"]).resolve()
            assert path.is_relative_to(generate_docs.PUBLIC_DIR.resolve())
            examples.append(json.loads(path.read_text()))
    assert examples
    for index, payload in enumerate(examples):
        if set(payload) in ({"outputs"}, {"provenance"}):
            assert_excerpt_matches(payload, examples[index + 1])
            continue
        if "metric" in payload and "input" in payload:
            validate(payload, TOOL_CONTRACTS_BY_NAME["lyra_run_metric"].input_schema)
            continue
        if "status" not in payload and "error" in payload:
            assert payload["error"]["code"] == "result_not_found"
            continue
        name = (
            "lyra_run_metric"
            if "status" not in payload and "next_tool" in payload
            else "lyra_download_result"
            if "format" in payload
            else "lyra_get_job_result"
        )
        validate(payload, TOOL_CONTRACTS_BY_NAME[name].output_schema)


def test_generated_field_tables_preserve_union_variants_and_constraints() -> None:
    schema = {
        "$defs": {
            "Active": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "const": "running"},
                    "note": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "One | two\nlines",
                    },
                    "limit": {"type": "integer", "minimum": 1, "default": 5},
                },
                "required": ["status"],
            },
            "Terminal": {
                "type": "object",
                "properties": {
                    "outputs": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["outputs"],
            },
        },
        "anyOf": [{"$ref": "#/$defs/Active"}, {"$ref": "#/$defs/Terminal"}],
    }
    rendered = "\n".join(schema_fields(schema, root=schema))

    assert "Variant: `Active`" in rendered
    assert "Variant: `Terminal`" in rendered
    assert '| `status` | `string` | yes | `—` | Constant: "running" |' in rendered
    assert r"| `note` | `string \| null` | no | `null` | One \| two lines |" in rendered
    assert "| `limit` | `integer` | no | `5` | minimum=1 |" in rendered
    assert "| `outputs` | `array of string` | yes |" in rendered


def test_published_local_tutorial_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = published_python("start/local.mdx", "first_metric.py")
    path = tmp_path / "first_metric.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("first_metric", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "first_metric", module)
    spec.loader.exec_module(module)

    assert list(module.tables) == ["maintenance"]
    assert module.tables["maintenance"].street_id.tolist() == ["street-1"]
    assert module.tables["maintenance"].maintenance_length_m.tolist() == [100.0]
    assert capsys.readouterr().out.strip()


def test_published_plugin_test_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    source = published_python("plugins/testing.md", "test_plugin.py")
    path = tmp_path / "plugin_test.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("plugin_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "plugin_test", module)
    spec.loader.exec_module(module)

    module.test_table_metric(module.streets.__wrapped__())


def test_published_graph_examples_run_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    source = published_python("plugins/testing.md", "test_graph.py")
    path = tmp_path / "documented_graph_test.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("documented_graph_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "documented_graph_test", module)
    spec.loader.exec_module(module)

    module.test_city_graph()
    module.test_city_source_failure()


def test_published_dependency_example_runs_with_a_fake_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    path = tmp_path / "maintenance_graph.py"
    path.write_text(
        published_python("plugins/dependencies.mdx", "maintenance_graph.py")
    )
    spec = importlib.util.spec_from_file_location("population_graph", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "population_graph", module)
    spec.loader.exec_module(module)

    assert module.tables["maintenance"].street_id.tolist() == ["street-1"]
    assert module.tables["maintenance"].maintenance_length_m.tolist() == [100.0]


def test_published_requests_validate_against_example_manifest() -> None:
    manifest = json.loads(render_manifest(CITY_PLUGIN_DIR))
    schemas = {metric["name"]: metric["request_schema"] for metric in manifest["nodes"]}
    requests = []
    for relative in ("plugins/authoring.md", "use/rest-api.md"):
        content = (CONTENT_DIR / relative).read_text()
        for block in re.findall(r"```json\n(.*?)\n```", content, re.DOTALL):
            payload = json.loads(block)
            if "metric" in payload and "input" in payload:
                validate(payload["input"], schemas[payload["metric"]])
                requests.append(payload)
    assert requests


@pytest.mark.usefixtures("installed_city_plugin")
def test_server_quickstart_request_runs_with_deployment_plugins() -> None:
    manifest = json.loads(render_manifest(CITY_PLUGIN_DIR))
    schemas = {node["name"]: node["request_schema"] for node in manifest["nodes"]}
    content = (CONTENT_DIR / "quickstart.mdx").read_text()
    bodies = re.findall(r"-d '(.*?)'", content, re.DOTALL)
    assert bodies
    config = LyraConfig.model_validate(
        tomllib.loads((ROOT / "examples/lyra-deployment/lyra.toml").read_text())
    )
    runner = LocalRunner(
        plugins=[item.distribution for item in config.plugins.installed if item.enabled]
    )
    for body in bodies:
        request = json.loads(body)
        validate(request["input"], schemas[request["metric"]])
        tables = runner.run(request["metric"], request["input"])
        assert tables["maintenance"].street_id.tolist() == ["street-1"]
        assert tables["maintenance"].maintenance_length_m.tolist() == [100.0]


@pytest.mark.usefixtures("installed_city_plugin")
def test_installed_plugin_guide_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = published_python("use/local-execution.md", "run_installed.py")
    path = tmp_path / "run_installed.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("run_installed", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "run_installed", module)
    spec.loader.exec_module(module)
    assert module.tables["maintenance"].street_id.tolist() == ["street-1"]
    assert module.tables["maintenance"].maintenance_length_m.tolist() == [100.0]


def test_plugin_quickstart_python_excerpts_are_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = generate_docs.normalize_mdx(
        (CONTENT_DIR / "plugins/quickstart.mdx").read_text()
    )
    blocks = re.findall(
        r"^[ \t]*```python[^\n]*\n(.*?)^[ \t]*```",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert blocks
    path = tmp_path / "explore_plugin.py"
    path.write_text("\n\n".join(dedent(block) for block in blocks))
    spec = importlib.util.spec_from_file_location("explore_plugin", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "explore_plugin", module)
    spec.loader.exec_module(module)
    runner = LocalRunner.from_definitions({"example": module.create_plugin()})
    tables = runner.run(
        "city_street_maintenance",
        {
            "streets": {
                "collection": "example:city",
                "unit_type": "street",
                "ids": ["street-1"],
            },
            "parameters": {"condition_threshold": 50},
        },
    )
    assert tables["maintenance"].street_id.tolist() == ["street-1"]
    assert tables["maintenance"].maintenance_length_m.tolist() == [100.0]


@pytest.mark.parametrize("chapter", range(1, 9))
def test_published_city_tutorial_builds_progressively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chapter: int,
) -> None:
    """Build each chapter's workspace and run its scripts and cumulative tests."""
    with city_tutorial_project(tmp_path, monkeypatch, chapter) as (sources, workspace):
        verify_city_tutorial_workspace(tmp_path, workspace, capsys, chapter, sources)


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


def verify_city_tutorial_workspace(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chapter: int,
    sources: dict[str, str],
) -> None:
    """Verify the current executable example and every test authored so far."""
    scripts = (
        ("first_metric", "lengths", "length_m", [100.0, 200.0, 300.0]),
        ("selected_metric", "lengths", "length_m", [300.0, 100.0]),
        ("maintenance_metric", "maintenance", "maintenance_length_m", [0.0, 100.0]),
        ("run_tutorial", "maintenance", "maintenance_length_m", [300.0, 100.0]),
        ("block_report", "summary", "maintenance_length_m", [100.0, 300.0, 0.0]),
        (
            "canonical_request",
            "maintenance",
            "maintenance_length_m",
            [100.0, 0.0, 300.0],
        ),
        (
            "inspect_tutorial",
            "maintenance",
            "maintenance_length_m",
            [100.0, 0.0, 300.0],
        ),
        (
            "canonical_request",
            "maintenance",
            "maintenance_length_m",
            [100.0, 0.0, 300.0],
        ),
    )
    name, output, column, expected = scripts[chapter - 1]
    module = load_documented_module(project / f"{name}.py", name, monkeypatch)
    frame = module.tables[output]
    assert frame[column].tolist() == expected
    assert frame.index.tolist() == list(range(len(expected)))
    identity = "block_id" if output == "summary" else "street_id"
    assert frame[identity].is_unique

    for filename in sources:
        if filename.startswith("tests/test_"):
            module = load_documented_module(
                project / filename, f"authored_{Path(filename).stem}", monkeypatch
            )
            for name, test in vars(module).items():
                if name.startswith("test_") and callable(test):
                    test()

    if chapter == 8:
        verify_city_tutorial_manifest(project, capsys)


def verify_city_tutorial_manifest(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the handoff commands against the authored source factory."""
    create_plugin = import_module("city_example").create_plugin
    assert create_plugin.__module__ == "city_example.tutorial"
    assert plugin_main(["build-manifest", "--project", str(project)]) == 0
    assert plugin_main(["check-manifest", "--project", str(project)]) == 0
    manifest = json.loads((project / "lyra.plugin.json").read_text())
    assert {
        node["name"]
        for node in manifest["nodes"]
        if node["kind"] == "metric" and not node["private"]
    } == {"city_street_lengths", "city_street_maintenance", "city_block_maintenance"}
    selector = next(
        node for node in manifest["nodes"] if node["name"] == "city_streets_for_blocks"
    )
    assert selector["canonical"] is True
    capsys.readouterr()
    assert (
        plugin_main(
            [
                "describe",
                "city_street_maintenance",
                "--project",
                str(project),
                "--json",
            ]
        )
        == 0
    )
    description = json.loads(capsys.readouterr().out)
    assert (
        description["nodes"][0]["handler"] == "city_example.tutorial.street_maintenance"
    )
    diagram = project / "graph.mmd"
    assert (
        plugin_main(
            [
                "diagram",
                "--project",
                str(project),
                "--detailed",
                "--output",
                str(diagram),
            ]
        )
        == 0
    )
    assert diagram.is_file()


@pytest.mark.parametrize("same_commit", [True, False])
@pytest.mark.parametrize("prebuilt", [True, False])
def test_two_target_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    same_commit: bool,
    prebuilt: bool,
) -> None:
    calls = []

    def build(ref: str, base: str, destination: Path, worktrees: Path) -> None:
        assert worktrees.is_dir()
        calls.append((ref, base))
        destination.mkdir()
        (destination / "index.html").write_text("<html><body>Docs</body></html>")

    monkeypatch.setattr(versioned_site, "build_ref", build)
    latest_sha = "a" * 40
    release_sha = latest_sha if same_commit else "b" * 40
    latest = None
    if prebuilt:
        latest = tmp_path / "validated"
        latest.mkdir()
        (latest / "index.html").write_text("<html><body>Validated</body></html>")
        (latest / "source-sha.txt").write_text(latest_sha)
        (latest / "source-base.txt").write_text("/Lyra/latest")
    output = tmp_path / "site"
    (output / "versions" / "old").mkdir(parents=True)
    versioned_site.assemble(output, latest_sha, "lyra-v0.8.0", release_sha, latest)
    assert calls == ([] if prebuilt else [(latest_sha, "/Lyra/latest")]) + [
        (release_sha, "/Lyra/versions/0.8.0")
    ]
    assert [p.name for p in (output / "versions").iterdir()] == ["0.8.0"]
    assert not (output / "versions.json").exists()
    assert "url=/Lyra/versions/0.8.0/" in (output / "index.html").read_text()
    assert (
        "Contains unreleased changes" in (output / "latest/index.html").read_text()
    ) != same_commit
    assert not broken_links(output, "/Lyra")
    if latest:
        assert "lyra-version-selector" not in (latest / "index.html").read_text()


@pytest.mark.parametrize("failure", ["identity", "build", "links", "tag", "sha"])
def test_assembly_failure_preserves_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "index.html").write_text("existing")
    latest = tmp_path / "latest"
    latest.mkdir()
    (latest / "index.html").write_text(
        '<html><body><a href="missing">bad</a></body></html>'
        if failure == "links"
        else "<html><body>Docs</body></html>"
    )
    (latest / "source-sha.txt").write_text(
        "wrong" if failure == "identity" else "a" * 40
    )
    (latest / "source-base.txt").write_text("/Lyra/latest")

    def build(_ref: str, _base: str, destination: Path, _worktrees: Path) -> None:
        if failure == "build":
            message = "build failed"
            raise RuntimeError(message)
        destination.mkdir()
        (destination / "index.html").write_text("<html><body>Docs</body></html>")

    monkeypatch.setattr(versioned_site, "build_ref", build)
    with pytest.raises((versioned_site.VersionedSiteError, RuntimeError)):
        versioned_site.assemble(
            output,
            "bad" if failure == "sha" else "a" * 40,
            "other" if failure == "tag" else "lyra-v0.8.0",
            "b" * 40,
            latest,
        )
    assert (output / "index.html").read_text() == "existing"


def test_config_defaults_match_actual_models() -> None:

    schema = LyraConfig.model_json_schema()
    rows = {row.path: row for row in config_rows(schema, root=schema)}
    assert rows["plugins.installed"].default == "[]"
    assert rows["api.forwarded_allow_ips"].default == '["127.0.0.1"]'
    assert (
        json.loads(rows["workers.<name>.concurrency"].default)
        == WorkerConfig(queues=["q"]).concurrency
    )


def test_release_build_cleans_worktree_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = []

    def run(*command: str, **_kwargs: object) -> None:
        commands.append(command)
        if command[:2] == ("npm", "ci"):
            message = "installation failed"
            raise RuntimeError(message)

    monkeypatch.setattr(versioned_site, "run", run)
    with pytest.raises(RuntimeError, match="installation failed"):
        versioned_site.build_ref(
            "a" * 40, "/Lyra/latest", tmp_path / "output", tmp_path
        )
    assert commands[-1] == (
        "git",
        "worktree",
        "remove",
        "--force",
        str(tmp_path / ("a" * 40)),
    )


@pytest.mark.parametrize("invalid_id", [None, "unknown", "block-a"])
def test_published_location_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_id: str | None
) -> None:
    """Exercise published bindings and provider failures without service access."""
    source = published_python("plugins/location-example.mdx", "location_example.py")
    path = tmp_path / "location_example.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("location_example", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "location_example", module)
    spec.loader.exec_module(module)
    events = []

    class FakePlaces(module.Places):
        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            _collection, unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
            events.append((unit_type, ids.copy()))
            return super().resolve(contract, ids)

        @override
        def close(self) -> None:
            events.append("close")

    def context_factory(_node: str) -> RunContext:
        assert "close" not in events
        return create_autospec(RunContext, instance=True)

    runner = LocalRunner.from_definitions(
        {"metrics": module.plugin, "geography": module.provider},
        resource_overrides={"places": FakePlaces},
        context_factory=context_factory,
    )
    request = deepcopy(module.request)
    if invalid_id is not None:
        request["lots"]["ids"].append(invalid_id)
        with pytest.raises(GraphExecutionError) as error:
            runner.run("block_population", request)
        assert error.value.node == "block_population"
        cause = error.value.__cause__
        assert isinstance(cause, UnresolvedLocationIDsError)
        assert cause.input_name == "lots"
        assert cause.ids == (invalid_id,)
    else:
        request["lots"]["ids"].append("lot-x")
        result = runner.run("block_population", request)
        assert result["block_totals"].to_dict("records") == [
            {"block_id": "block-a", "population": 8}
        ]
        assert events[1:-1] == [("block", ["block-a"]), ("lot", ["lot-x", "lot-y"])]
    assert events[0] == "setup"
    assert events[-1] == "close"


def test_published_derived_location_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the operation tutorial with in-memory provider fixtures."""
    monkeypatch.syspath_prepend(str(ROOT / "examples/location-plugin"))
    source = published_python("plugins/location-operations.md", "derived_example.py")
    path = tmp_path / "derived_example.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("derived_example", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "derived_example", module)
    spec.loader.exec_module(module)
    empty = module.runner.run(
        "block_score",
        {
            "blocks": {
                "collection": "city:entities",
                "unit_type": "block",
                "ids": ["b-empty"],
            }
        },
    )
    assert empty["block_scores"].to_dict("records") == [{"id": "b-empty", "score": 0.0}]
