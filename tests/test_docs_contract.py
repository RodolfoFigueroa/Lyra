from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import Mock, create_autospec

import pandas as pd
import pytest
from jsonschema import validate
from lyra.sdk import DatasetContext, LocalRunner, PluginDefinition, RunContext
from lyra.sdk.db import LyraDB
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.plugin_cli import render_manifest

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
    version_manifest,
)
from lyra_app.config import LyraConfig
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR, feature_collection

ROOT = Path(__file__).parents[1]


def published_python(relative: str, filename: str) -> str:
    """Assemble consecutive file excerpts, including code nested in MDX steps."""
    content = (CONTENT_DIR / relative).read_text()
    blocks = re.findall(
        r"^[ \t]*```python([^\n]*)\n(.*?)^[ \t]*```",
        content,
        re.MULTILINE | re.DOTALL,
    )
    sources = [
        dedent(source).strip()
        for metadata, source in blocks
        if f'title="{filename}"' in metadata
    ]
    assert sources, f"No excerpts found for {filename} in {relative}"
    return "\n\n".join(sources)


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
        for slug in group["items"]
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

    assert broken_links(tmp_path, "/lyra/latest") == [
        "index.html -> absent/",
        "index.html -> guide/#missing",
    ]


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
    monkeypatch.setenv("LYRA_DOCS_BASE", "/lyra/versions/test/")

    generate_docs.generate_llm_files()

    for name in ("llms.txt", "llms-full.txt"):
        content = (tmp_path / name).read_text()
        assert "/lyra/versions/test/api/lyra/llms.txt" in content
        assert "reference/generated/python" not in content


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


def test_cli_reference_includes_nested_admin_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    generate_docs.generate_cli_reference()
    reference = (tmp_path / "cli.md").read_text()

    assert "## `lyra-admin`\n" in reference
    assert "## `lyra-admin plugins list`\n" in reference
    assert "## `lyra-admin config validate`\n" in reference
    assert "## `lyra-admin workers restart`\n" not in reference
    assert "--restart-timeout" not in reference
    assert "## `lyra-plugin`\n" in reference
    assert "lyra-tui" not in reference


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
    assert "earth_engine.project" in paths


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


def test_release_parser_accepts_product_and_legacy_application_tags() -> None:
    assert parse_release("lyra-v0.8.0") == Release((0, 8, 0), "lyra-v0.8.0")
    assert parse_release("lyra-app-v0.7.0") == Release((0, 7, 0), "lyra-app-v0.7.0")
    assert parse_release("lyra-sdk-v9.0.0") is None
    assert parse_release("lyra-api-v9.0.0") is None
    assert parse_release("lyra-app-v0.5.0") is None
    assert parse_release("lyra-v1.0") is None


def test_version_selector_marks_each_tree_and_is_idempotent(tmp_path: Path) -> None:
    releases = [
        Release((0, 7, 0), "lyra-v0.7.0"),
        Release((0, 6, 0), "lyra-app-v0.6.0"),
    ]
    manifest = version_manifest(releases)
    page = tmp_path / "index.html"
    page.write_text("<html><body><main>Lyra</main></body></html>")

    inject_selector(tmp_path, manifest, "/lyra/latest")
    inject_selector(tmp_path, manifest, "/lyra/latest")

    rendered = page.read_text()
    assert rendered.count('class="lyra-version-selector"') == 1
    assert '<option value="/lyra/latest" selected>' in rendered
    assert {item["base"] for item in manifest} == {
        "/lyra/latest",
        "/lyra/versions/0.7.0",
        "/lyra/versions/0.6.0",
    }


@pytest.mark.parametrize(
    ("relative", "expected_count"),
    [("use/mcp.md", 2), ("reference/mcp-payloads.md", 13)],
)
def test_mcp_workflow_examples_match_serialization_schemas(
    relative: str, expected_count: int
) -> None:
    page = (CONTENT_DIR / relative).read_text()
    examples = re.findall(r"```json\n(.*?)\n```", page, re.DOTALL)
    assert len(examples) == expected_count
    for index, example in enumerate(examples):
        payload = json.loads(example)
        if set(payload) in ({"outputs"}, {"provenance"}):
            assert_excerpt_matches(payload, json.loads(examples[index + 1]))
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


def test_generated_mcp_reference_keeps_readable_fields_and_full_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    generate_docs.generate_mcp_reference()
    content = (tmp_path / "mcp.md").read_text()
    schemas = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", content, re.DOTALL)
    ]

    assert content.count("<details>") == 2 * len(TOOL_CONTRACTS_BY_NAME)
    for contract in TOOL_CONTRACTS_BY_NAME.values():
        assert f"## `{contract.name}`" in content
        assert contract.input_schema in schemas
        assert contract.output_schema in schemas
        for field in contract.input_schema.get("properties", {}):
            assert f"| `{field}` |" in content
    assert "Variant: `ActiveResultOutput`" in content
    assert "Variant: `TerminalResultOutput`" in content


def test_generated_http_reference_includes_inputs_and_every_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    schema = generate_docs.generate_http_reference()
    content = (tmp_path / "http.md").read_text()

    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert f"### `{method.upper()} {path}`" in content
            assert f"## {operation['tags'][0]}" in content
    submission = content.split("### `POST /jobs`", 1)[1].split("\n### ", 1)[0]
    assert "**Authentication**: Agent Bearer key (`LYRA_AGENT_API_KEY`)." in submission
    assert "| `metric` | `string` | yes |" in submission
    assert "| `input` | `object` | yes |" in submission
    assert "| 202 |" in submission
    readiness = content.split("### `GET /ready`", 1)[1].split("\n### ", 1)[0]
    assert "| 503 |" in readiness
    assert "ReadinessResponse" in readiness


def test_generated_config_reference_keeps_all_fields_and_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_docs, "GENERATED_DIR", tmp_path)
    schema = generate_docs.generate_config_reference()
    content = (tmp_path / "configuration.md").read_text()

    for row in config_rows(schema, root=schema):
        assert f"### `{row.path}`" in content
        assert f"{row.description.rstrip('.')}:" in content
        assert f"- Default: `{generate_docs.markdown_cell(row.default)}`" in content
        assert (
            f"- Constraints: {generate_docs.markdown_cell(row.constraints)}" in content
        )
    assert all(variable in content for variable in ENV_FIELDS)


def test_published_local_tutorial_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    content = (CONTENT_DIR / "start/local.mdx").read_text()
    source = published_python("start/local.mdx", "first_metric.py")
    path = tmp_path / "first_metric.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("first_metric", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "first_metric", module)
    spec.loader.exec_module(module)

    assert list(module.tables) == ["counts"]
    assert module.tables["counts"].to_dict(orient="list") == {"count": [3]}
    expected = dedent(
        re.findall(
            r"^[ \t]*```text\n(.*?)^[ \t]*```", content, re.MULTILINE | re.DOTALL
        )[0]
    )
    assert capsys.readouterr().out.strip() == expected.strip()


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

    module.test_table_metric(module.region.__wrapped__())


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

    module.test_population_graph(tmp_path)
    module.test_population_source_failure(tmp_path)


def test_published_dataset_example_runs_with_a_fake_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    path = tmp_path / "population_graph.py"
    path.write_text(published_python("plugins/datasets.mdx", "population_graph.py"))
    spec = importlib.util.spec_from_file_location("population_graph", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "population_graph", module)
    spec.loader.exec_module(module)

    context = create_autospec(DatasetContext, instance=True)
    context.db = create_autospec(LyraDB, instance=True)
    context.db.query_dataframe.return_value = pd.DataFrame(
        {"area_code": ["area_a"], "population": [123]}
    )
    runner = LocalRunner.from_definitions(
        {"example": module.plugin},
        context_factory=lambda _node, role: (
            context if role == "dataset" else create_autospec(RunContext, instance=True)
        ),
    )
    result = runner.run("example_city_population", {"parameters": {"year": 2020}})
    assert result["example_city_population"]["population"].tolist() == [123]
    assert context.db.query_dataframe.call_args.kwargs["params"] == {"year": 2020}


def test_published_requests_validate_against_example_manifest() -> None:
    manifest = json.loads(render_manifest(SMOKE_PLUGIN_DIR))
    schemas = {metric["name"]: metric["request_schema"] for metric in manifest["nodes"]}
    requests = []
    for relative in ("plugins/authoring.md", "use/rest-api.md"):
        content = (CONTENT_DIR / relative).read_text()
        for block in re.findall(r"```json\n(.*?)\n```", content, re.DOTALL):
            payload = json.loads(block)
            if "metric" in payload and "input" in payload:
                validate(payload["input"], schemas[payload["metric"]])
                requests.append(payload)
    assert {request["metric"] for request in requests} == {
        "smoke_table_metric",
        "smoke_summary_metric",
    }


def test_published_authoring_adapter_runs_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = published_python("plugins/authoring.md", "metric.py")
    path = tmp_path / "documented_plugin.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("documented_plugin", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "documented_plugin", module)
    spec.loader.exec_module(module)

    plugin = PluginDefinition(nodes=[module.run_table])
    location = GeoJSON.model_validate(feature_collection(("a", "b")))
    context = create_autospec(RunContext, instance=True)
    parameters = plugin.prepare_parameters("smoke_table_metric", {"value": 7})
    frame = module.run_table(parameters=parameters, location=location, context=context)
    result = plugin.normalize_result("smoke_table_metric", frame)
    assert isinstance(result, dict)
    result = next(iter(result.values()))
    assert result.to_numpy().tolist() == [[7], [7]]
    assert result.index.tolist() == ["a", "b"]


def test_versioned_assembly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    releases = [Release((0, 8, 0), "lyra-v0.8.0"), Release((0, 7, 0), "lyra-v0.7.0")]
    monkeypatch.setattr(versioned_site, "discover_releases", lambda: releases)
    calls: list[tuple[str, str]] = []

    def fake_build(ref: str, base: str, destination: Path, worktrees: Path) -> None:
        assert worktrees.is_dir()
        calls.append((ref, base))
        destination.mkdir()
        (destination / "index.html").write_text("<html><body>Docs</body></html>")

    monkeypatch.setattr(versioned_site, "build_ref", fake_build)
    output = tmp_path / "site"
    versioned_site.assemble(output, "main")
    assert calls == [("main", "/lyra/latest"), *[(r.tag, r.base) for r in releases]]
    assert sorted(p.name for p in output.iterdir()) == [
        "index.html",
        "latest",
        "versions",
        "versions.json",
    ]
    assert "url=/lyra/versions/0.8.0/" in (output / "index.html").read_text()
    assert (
        "May include unreleased changes" in (output / "latest/index.html").read_text()
    )
    for release in releases:
        page = (output / "versions" / release.label / "index.html").read_text()
        assert "May include unreleased changes" not in page
        assert f'<option value="{release.base}" selected>' in page
    assert json.loads((output / "versions.json").read_text()) == version_manifest(
        releases
    )
    assert (
        versioned_site.build_parser().parse_args(["--output", "site"]).latest_ref
        == "main"
    )

    monkeypatch.setattr(versioned_site, "discover_releases", list)
    with pytest.raises(versioned_site.VersionedSiteError, match="No supported"):
        versioned_site.assemble(output, "main")
    assert (output / "latest/index.html").exists()

    monkeypatch.setattr(versioned_site, "discover_releases", lambda: releases)

    def failed_build(ref: str, base: str, destination: Path, worktrees: Path) -> None:
        del ref, base, destination, worktrees
        message = "Build failed"
        raise RuntimeError(message)

    monkeypatch.setattr(versioned_site, "build_ref", failed_build)
    with pytest.raises(RuntimeError, match="Build failed"):
        versioned_site.assemble(output, "main")
    assert (output / "latest/index.html").exists()


def test_archive_cache_reuses_only_complete_matching_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release((0, 8, 0), "lyra-v0.8.0")
    git = Mock(return_value=Mock(stdout="a" * 40))
    monkeypatch.setattr(versioned_site, "run", git)
    calls: list[str] = []

    def build(ref: str, base: str, destination: Path, worktrees: Path) -> None:
        del base, worktrees
        calls.append(ref)
        destination.mkdir()
        (destination / "index.html").write_text("<html><body>archive</body></html>")

    monkeypatch.setattr(versioned_site, "build_ref", build)
    cache = tmp_path / "cache"
    versioned_site.cached_build(release, tmp_path / "first", tmp_path, cache)
    versioned_site.cached_build(release, tmp_path / "second", tmp_path, cache)
    assert calls == [release.tag]
    next(cache.glob("*/complete")).unlink()
    versioned_site.cached_build(release, tmp_path / "third", tmp_path, cache)
    assert calls == [release.tag] * 2
    git.return_value.stdout = "b" * 40
    versioned_site.cached_build(release, tmp_path / "fourth", tmp_path, cache)
    assert calls == [release.tag] * 3
    assert "lyra-version-selector" not in (tmp_path / "fourth/index.html").read_text()

    monkeypatch.setenv("LYRA_DOCS_CACHE_TOOLCHAIN", "new-toolchain")
    versioned_site.cached_build(release, tmp_path / "fifth", tmp_path, cache)
    assert calls == [release.tag] * 3
    monkeypatch.setattr(versioned_site, "ARCHIVE_RECIPE_VERSION", "next")
    versioned_site.cached_build(release, tmp_path / "recipe", tmp_path, cache)
    assert calls == [release.tag] * 4
    for page in cache.glob("*/site/index.html"):
        page.unlink()
    versioned_site.cached_build(release, tmp_path / "sixth", tmp_path, cache)
    assert calls == [release.tag] * 5


def test_prebuilt_latest_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release((0, 8, 0), "lyra-v0.8.0")
    monkeypatch.setattr(versioned_site, "discover_releases", lambda: [release])
    latest = tmp_path / "latest"
    latest.mkdir()
    (latest / "index.html").write_text("<html><body>validated latest</body></html>")
    calls: list[str] = []

    def build(ref: str, base: str, destination: Path, worktrees: Path) -> None:
        del base, worktrees
        calls.append(ref)
        destination.mkdir()
        (destination / "index.html").write_text("<html><body>release</body></html>")

    monkeypatch.setattr(versioned_site, "build_ref", build)
    versioned_site.assemble(tmp_path / "site", "main", latest)
    assert calls == [release.tag]
    assert "validated latest" in (tmp_path / "site/latest/index.html").read_text()
    assert "lyra-version-selector" not in (latest / "index.html").read_text()
