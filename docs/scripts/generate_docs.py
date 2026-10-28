"""Generate all source-derived Lyra documentation and machine contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from lyra.api.admin_cli import build_parsers as build_admin_parsers
from lyra.sdk.config import LyraConfig
from lyra.sdk.plugin_cli import build_parser as build_plugin_parser

from lyra_app.config import LyraConfig as RuntimeConfig
from lyra_app.mcp.models import TOOL_CONTRACTS
from lyra_app.mcp.server import SERVER_INSTRUCTIONS
from lyra_app.routes import admin, data_types, health, jobs, met_zone, metrics
from lyra_app.version import APP_VERSION
from lyra_app.worker_launcher import build_parser as build_worker_parser

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
CONTENT_DIR = DOCS_DIR / "src" / "content" / "docs"
GENERATED_DIR = CONTENT_DIR / "reference" / "generated"
PUBLIC_DIR = DOCS_DIR / "public"
NAVIGATION_PATH = DOCS_DIR / "navigation.json"
SITE = "https://rodolfofigueroa.github.io"
DEFAULT_BASE = "/lyra/latest"
PYTHON_API_SLUG = "api/lyra"

DEFAULT_OVERRIDES: dict[str, object] = {
    "api.forwarded_allow_ips": ["127.0.0.1"],
    "database.api.pool_size": 5,
    "database.api.max_overflow": 0,
    "database.api.pool_timeout_seconds": 2.0,
    "database.api.connect_timeout_seconds": 5,
    "database.api.statement_timeout_ms": 10_000,
    "database.api.pool_recycle_seconds": 900,
    "database.spatial.pool_size": 2,
    "database.spatial.max_overflow": 0,
    "database.spatial.pool_timeout_seconds": 2.0,
    "database.spatial.connect_timeout_seconds": 5,
    "database.spatial.statement_timeout_ms": 25_000,
    "database.spatial.pool_recycle_seconds": 900,
    "database.worker.pool_size": 1,
    "database.worker.max_overflow": 0,
    "database.worker.pool_timeout_seconds": 5.0,
    "database.worker.connect_timeout_seconds": 5,
    "database.worker.statement_timeout_ms": 300_000,
    "database.worker.pool_recycle_seconds": 900,
    "plugins.installed": [],
}

ENV_FIELDS = {
    "database.password": ("LYRA_POSTGRES_PASSWORD", True),
    "admin.api_key": ("LYRA_ADMIN_API_KEY", True),
    "agent.api_key": ("LYRA_AGENT_API_KEY", True),
}


class DocumentationGenerationError(ValueError):
    """Raised when documentation inputs violate the generation contract."""


@dataclass(frozen=True)
class ConfigRow:
    """Describe one leaf configuration field for the generated reference table."""

    path: str
    type_name: str
    required: bool
    default: str
    constraints: str
    description: str


def main() -> None:
    """Generate all source-derived documentation pages and public contracts."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    remove_legacy_python_reference()
    openapi = generate_http_reference()
    config_schema = generate_config_reference()
    generate_cli_reference()
    generate_mcp_reference()
    write_json(PUBLIC_DIR / "openapi.json", openapi)
    write_json(PUBLIC_DIR / "config.schema.json", config_schema)
    write_json(
        PUBLIC_DIR / "mcp-tools.json",
        {
            "instructions": SERVER_INSTRUCTIONS,
            "tools": [
                {
                    "name": contract.name,
                    "description": contract.description,
                    "read_only": contract.read_only,
                    "idempotent": contract.idempotent,
                    "open_world": contract.open_world,
                    "input_schema": contract.input_schema,
                    "output_schema": contract.output_schema,
                }
                for contract in TOOL_CONTRACTS
            ],
        },
    )
    generate_llm_files()


def remove_legacy_python_reference() -> None:
    """Remove output left by the retired Python API renderer."""
    legacy = GENERATED_DIR / "python"
    if legacy.is_dir():
        shutil.rmtree(legacy)


def write_text(path: Path, content: str) -> None:
    """Write UTF-8 text after normalizing it to one trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content.rstrip()}\n", encoding="utf-8")


def write_json(path: Path, content: object) -> None:
    """Serialize an object as deterministic, indented JSON."""
    write_text(path, json.dumps(content, indent=2, sort_keys=True))


def frontmatter(title: str, description: str, content_plan: str) -> str:
    """Build the required frontmatter for a generated documentation page.

    Returns:
        YAML metadata linking a reference page to its editorial plan.
    """
    label = re.sub(
        r"\b(?:Api|Cli|Http|Mcp)\b", lambda match: match[0].upper(), title.title()
    )
    return (
        f"---\ntitle: {title}\ndescription: {description}\n"
        f"contentType: Reference\ncontentPlan: {content_plan}\n"
        f"sidebar: {{ label: {json.dumps(label)} }}\n---\n"
    )


def create_openapi_app() -> FastAPI:
    """Create a schema-only FastAPI application containing every public router.

    Returns:
        An application suitable for generating the complete OpenAPI document.
    """
    app = FastAPI(title="Lyra API", version=APP_VERSION)
    for router in (
        admin.router,
        health.router,
        jobs.router,
        data_types.router,
        metrics.router,
        met_zone.router,
    ):
        app.include_router(router)
    return app


def generate_http_reference() -> dict[str, Any]:
    """Generate HTTP operation details and the complete OpenAPI schema.

    Returns:
        The generated OpenAPI schema written alongside the Markdown reference.
    """
    schema = create_openapi_app().openapi()
    lines = [
        frontmatter(
            "HTTP API reference",
            "Generated routes, authentication boundaries, and OpenAPI operations.",
            "reference/generated/http",
        ),
        (
            "Use this reference to look up a route, its inputs, "
            "and its authentication. "
            "For a first request, follow [the server tutorial](../../../quickstart/). "
            "Download [the complete OpenAPI contract](../../../openapi.json) "
            "for nested request and response schemas."
        ),
        "",
    ]
    groups: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, path_item in sorted(schema["paths"].items()):
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            group = next(iter(operation.get("tags", [])), "Other routes")
            groups.setdefault(group, []).append((path, method, operation))
    for group, operations in groups.items():
        lines.extend([f"## {group}", ""])
        for path, method, operation in operations:
            lines.extend([f"### `{method.upper()} {path}`", ""])
            summary = " ".join(
                operation.get("description", "").split("\n\n", 1)[0].split()
            )
            lines.extend(
                [summary or operation.get("summary", operation["operationId"]), ""]
            )
            security = operation.get("security", [])
            schemes = sorted(item for entry in security for item in entry)
            credentials = {
                "AgentBearer": "Agent Bearer key (`LYRA_AGENT_API_KEY`)",
                "AdminBearer": "Administrator Bearer key (`LYRA_ADMIN_API_KEY`)",
            }
            authentication = ", ".join(credentials.get(name, name) for name in schemes)
            authentication = authentication or "Public; no key required"
            lines.extend(
                [
                    f"**Authentication**: {authentication}.",
                    "",
                ]
            )
            parameters = operation.get("parameters", [])
            if parameters:
                fields = {
                    "properties": {
                        item["name"]: {
                            **item.get("schema", {}),
                            "description": (
                                f"{item['in']} parameter. {item.get('description', '')}"
                            ).strip(),
                        }
                        for item in parameters
                    },
                    "required": [
                        item["name"] for item in parameters if item.get("required")
                    ],
                }
                lines.extend(
                    ["#### Parameters", "", *schema_fields(fields, root=schema)]
                )
            body = operation.get("requestBody", {})
            body_schema = (
                body.get("content", {}).get("application/json", {}).get("schema")
            )
            if body_schema:
                lines.extend(
                    [
                        "#### JSON request body",
                        "",
                        *schema_fields(body_schema, root=schema),
                    ]
                )
            lines.extend(
                [
                    "#### Responses",
                    "",
                    "| Status | Meaning | JSON schema |",
                    "| --- | --- | --- |",
                ]
            )
            for code, response in operation.get("responses", {}).items():
                response_schema = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                type_name = schema_type(response_schema) if response_schema else "—"
                lines.append(
                    f"| {code} | {markdown_cell(response.get('description', ''))} "
                    f"| `{markdown_cell(type_name)}` |"
                )
            lines.append("")
    write_text(GENERATED_DIR / "http.md", "\n".join(lines))
    return schema


def resolve_schema(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local JSON Schema reference against its root document.

    Returns:
        The referenced definition, or ``schema`` itself when it is inline.

    Raises:
        DocumentationGenerationError: If the reference is not a supported local
            definition reference.
    """
    reference = schema.get("$ref")
    if reference is None:
        return schema
    for prefix, definitions in (
        ("#/$defs/", root.get("$defs", {})),
        ("#/components/schemas/", root.get("components", {}).get("schemas", {})),
    ):
        if reference.startswith(prefix):
            return definitions[reference.removeprefix(prefix)]
    message = f"Unsupported schema reference: {reference}"
    raise DocumentationGenerationError(message)


def schema_type(schema: dict[str, Any]) -> str:
    """Render a JSON Schema's effective type as compact display text.

    Returns:
        A type name or union assembled from the schema structure.
    """
    if "type" in schema:
        value = schema["type"]
        if value == "array":
            return f"array of {schema_type(schema.get('items', {}))}"
        return " | ".join(value) if isinstance(value, list) else str(value)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            return " | ".join(schema_type(item) for item in schema[keyword])
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return "object"


def schema_constraints(schema: dict[str, Any]) -> str:
    """Render supported validation constraints from a JSON Schema field.

    Returns:
        A comma-separated list of constraint assignments.
    """
    names = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
    )
    return ", ".join(f"{name}={schema[name]}" for name in names if name in schema)


def markdown_cell(value: str) -> str:
    """Escape table separators, markup, and newlines in a generated cell.

    Returns:
        A single-line value suitable for a Markdown table.
    """
    return (
        " ".join(value.split())
        .replace("|", r"\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def schema_fields(schema: dict[str, Any], *, root: dict[str, Any]) -> list[str]:
    """Describe top-level contract fields while preserving downloadable schemas.

    Returns:
        Markdown field tables, including separate tables for union variants.
    """
    resolved = resolve_schema(schema, root)
    for keyword in ("anyOf", "oneOf"):
        if keyword in resolved:
            lines = []
            for variant in resolved[keyword]:
                lines.extend(
                    [
                        f"**Variant: `{schema_type(variant)}`**",
                        "",
                        *schema_fields(variant, root=root),
                    ]
                )
            return lines
    properties = resolved.get("properties", {})
    if not properties:
        return ["No named fields. See the full schema for the accepted value.", ""]
    required = set(resolved.get("required", []))
    lines = [
        "| Field | Type | Required | Default | Description and constraints |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, field in properties.items():
        effective = {**resolve_schema(field, root), **field}
        details = effective.get("description", "")
        constraints = schema_constraints(effective)
        choices = effective.get("enum")
        if choices is not None:
            constraints = f"{constraints} Choices: {json.dumps(choices)}".strip()
        if "const" in effective:
            constraints = (
                f"{constraints} Constant: {json.dumps(effective['const'])}"
            ).strip()
        details = f"{details} {constraints}".strip() or "—"
        default = json.dumps(effective["default"]) if "default" in effective else "—"
        lines.append(
            f"| `{markdown_cell(name)}` | `{markdown_cell(schema_type(field))}` "
            f"| {'yes' if name in required else 'no'} | `{markdown_cell(default)}` "
            f"| {markdown_cell(details)} |"
        )
    return [*lines, ""]


def config_rows(
    schema: dict[str, Any],
    *,
    root: dict[str, Any],
    prefix: str = "",
    parent_required: bool = True,
) -> list[ConfigRow]:
    """Flatten nested configuration schema fields into documentation rows.

    Returns:
        Leaf configuration fields with inherited requiredness and metadata.

    Raises:
        DocumentationGenerationError: If a leaf field has no description.
    """
    resolved = resolve_schema(schema, root)
    properties = resolved.get("properties", {})
    required = set(resolved.get("required", []))
    rows: list[ConfigRow] = []
    for name, raw_property in properties.items():
        path = f"{prefix}.{name}" if prefix else name
        property_schema = resolve_schema(raw_property, root)
        nested = property_schema.get("properties")
        if nested is not None:
            rows.extend(
                config_rows(
                    raw_property,
                    root=root,
                    prefix=path,
                    parent_required=parent_required and name in required,
                )
            )
            continue
        if path == "workers":
            worker_schema = property_schema.get("additionalProperties", {})
            rows.extend(
                config_rows(
                    worker_schema,
                    root=root,
                    prefix="workers.<name>",
                    parent_required=parent_required and name in required,
                )
            )
            continue
        description = property_schema.get("description", "").strip()
        if not description:
            message = f"Config field lacks a description: {path}"
            raise DocumentationGenerationError(message)
        default_value = property_schema.get("default", DEFAULT_OVERRIDES.get(path, "—"))
        default = (
            json.dumps(default_value, sort_keys=True)
            if default_value != "—"
            else default_value
        )
        rows.append(
            ConfigRow(
                path=path,
                type_name=schema_type(raw_property),
                required=parent_required and name in required,
                default=default,
                constraints=schema_constraints(property_schema) or "—",
                description=description,
            )
        )
    return rows


def generate_config_reference() -> dict[str, Any]:
    """Generate the TOML and environment configuration reference.

    Returns:
        The complete JSON Schema generated from ``LyraConfig``.

    Raises:
        DocumentationGenerationError: If an environment-owned field is absent
            from the configuration schema.
    """
    schema = LyraConfig.model_json_schema()
    rows = config_rows(schema, root=schema)
    runtime_schema = RuntimeConfig.model_json_schema()
    runtime_rows = config_rows(runtime_schema, root=runtime_schema)
    known_paths = {row.path for row in runtime_rows}
    missing_env = sorted(set(ENV_FIELDS) - known_paths)
    if missing_env:
        message = f"Unknown environment config fields: {missing_env}"
        raise DocumentationGenerationError(message)

    toml_rows = [row for row in rows if row.path not in ENV_FIELDS]
    lines = [
        frontmatter(
            "Configuration reference",
            "Generated TOML fields, environment variables, defaults, and constraints.",
            "reference/generated/configuration",
        ),
        (
            "Lyra reads settings from `/lyra_data/config/lyra.toml` once at startup. "
            "Database passwords and API credentials come from environment variables."
        ),
        "",
        (
            "For setup and secret ownership, read "
            "[Configure credentials and networking](../../../operate/configuration/). "
            "Download the [complete configuration schema](../../../config.schema.json) "
            "for nested types."
        ),
        "",
    ]
    sections: dict[str, list[ConfigRow]] = {}
    for row in toml_rows:
        sections.setdefault(row.path.partition(".")[0], []).append(row)
    for section, fields in sections.items():
        lines.extend([f"## `{section}`", ""])
        for row in fields:
            lines.extend(
                [
                    f"### `{row.path}`",
                    "",
                    f"{row.description.rstrip('.')}:",
                    "",
                    f"- Type: `{markdown_cell(row.type_name)}`",
                    f"- Required: {'yes' if row.required else 'no'}",
                    f"- Default: `{markdown_cell(row.default)}`",
                    f"- Constraints: {markdown_cell(row.constraints)}",
                    "",
                ]
            )
    lines.extend(
        [
            "",
            "## Environment variables",
            "",
            "| Variable | Config value | Secret | Description |",
            "| --- | --- | --- | --- |",
        ]
    )
    by_path = {row.path: row for row in runtime_rows}
    for path, (variable, secret) in ENV_FIELDS.items():
        lines.append(
            f"| `{variable}` | `{path}` | {'yes' if secret else 'no'} | "
            f"{by_path[path].description} |"
        )
    write_text(GENERATED_DIR / "configuration.md", "\n".join(lines))
    return schema


def generate_cli_reference() -> None:
    """Generate formatted help for every supported command-line interface."""
    parsers = (
        ("lyra-plugin", build_plugin_parser()),
        *((parser.prog, parser) for parser in build_admin_parsers()),
        ("worker launcher", build_worker_parser()),
    )
    lines = [
        frontmatter(
            "CLI reference",
            "Generated help for Lyra's supported command-line interfaces.",
            "reference/generated/cli",
        ),
        (
            "Use `lyra-plugin` for authoring, `lyra-admin` for server inspection, and "
            "the worker launcher to start a configured pool."
        ),
        "",
        (
            "For procedures, read [Test a plugin](../../../plugins/testing/) or "
            "[Use the administrative CLI](../../../operate/admin-cli/). "
            "The help below comes from the command parsers."
        ),
        "",
    ]
    for title, parser in parsers:
        help_text = parser.format_help().rstrip()
        expandable = len(help_text.splitlines()) > 25
        lines.extend(
            [
                f"## `{title}`",
                "",
                parser.description or f"Inspect help for `{title}`.",
                "",
                *(
                    ["<details>", "<summary>Complete command help</summary>", ""]
                    if expandable
                    else []
                ),
                "```text",
                help_text,
                "```",
                "",
                *(["</details>", ""] if expandable else []),
            ]
        )
    write_text(GENERATED_DIR / "cli.md", "\n".join(lines))


def generate_mcp_reference() -> None:
    """Generate readable MCP fields and expandable serialization schemas."""
    lines = [
        frontmatter(
            "MCP tool reference",
            "Generated tool inputs, outputs, and complete serialization schemas.",
            "reference/generated/mcp",
        ),
        (
            "The Model Context Protocol (MCP) endpoint uses Streamable HTTP with "
            "`LYRA_AGENT_API_KEY` Bearer authentication. "
            "Read [the MCP workflow](../../../use/mcp/) for discovery, submission, and "
            "result access, or inspect "
            "[complete payload examples](../../mcp-payloads/)."
        ),
        "",
        (
            "The tables summarize top-level fields. Expand a schema for all nested "
            "definitions, or download "
            "[the complete tool contracts](../../../mcp-tools.json)."
        ),
    ]
    for contract in TOOL_CONTRACTS:
        annotations = ["read-only" if contract.read_only else "writes state"]
        if contract.idempotent:
            annotations.append("idempotent")
        if contract.open_world:
            annotations.append("open-world")
        lines.extend(
            [
                "",
                f"## `{contract.name}`",
                "",
                contract.description,
                "",
                f"Annotations: {', '.join(annotations)}.",
                "",
                "### Input",
                "",
                *schema_fields(contract.input_schema, root=contract.input_schema),
                "<details>",
                "<summary>Complete input schema</summary>",
                "",
                "```json",
                json.dumps(contract.input_schema, indent=2, sort_keys=True),
                "```",
                "",
                "</details>",
                "",
                "### Output",
                "",
                *schema_fields(contract.output_schema, root=contract.output_schema),
                "<details>",
                "<summary>Complete output schema</summary>",
                "",
                "```json",
                json.dumps(contract.output_schema, indent=2, sort_keys=True),
                "```",
                "",
                "</details>",
                "",
            ]
        )
    write_text(GENERATED_DIR / "mcp.md", "\n".join(lines))


def read_frontmatter(path: Path) -> tuple[str, str, str]:
    """Read a page's required metadata and Markdown body.

    Returns:
        The page title, description, and stripped body.

    Raises:
        DocumentationGenerationError: If frontmatter is missing, malformed, or
            lacks a title or description.
    """
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, flags=re.DOTALL)
    if match is None:
        message = f"Missing frontmatter: {path.relative_to(ROOT)}"
        raise DocumentationGenerationError(message)
    metadata, body = match.groups()
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            message = f"Invalid frontmatter line in {path}: {line}"
            raise DocumentationGenerationError(message)
        parsed_value = value.strip()
        if parsed_value.startswith('"'):
            parsed_value = json.loads(parsed_value)
        fields[key.strip()] = parsed_value
    try:
        return fields["title"], fields["description"], body.strip()
    except KeyError as exc:
        message = f"Incomplete frontmatter: {path.relative_to(ROOT)}"
        raise DocumentationGenerationError(message) from exc


def navigation_pages() -> list[Path]:
    """Resolve navigation entries to their source documentation pages.

    Returns:
        Pages in navigation order, with generated sections expanded by filename.

    Raises:
        DocumentationGenerationError: If a configured page does not exist.
    """
    navigation = json.loads(NAVIGATION_PATH.read_text(encoding="utf-8"))
    pages: list[Path] = []
    for group in navigation:
        for slug in group["items"]:
            if slug == PYTHON_API_SLUG:
                continue
            if slug == "reference/generated":
                pages.extend(sorted(GENERATED_DIR.rglob("*.md")))
                continue
            candidates = (CONTENT_DIR / f"{slug}.md", CONTENT_DIR / f"{slug}.mdx")
            page = next(
                (candidate for candidate in candidates if candidate.is_file()), None
            )
            if page is None:
                message = f"Navigation page does not exist: {slug}"
                raise DocumentationGenerationError(message)
            pages.append(page)
    return pages


def page_slug(path: Path) -> str:
    """Convert a documentation source path to its site-relative URL slug.

    Returns:
        The normalized slug, or an empty string for the site index.
    """
    relative = path.relative_to(CONTENT_DIR).with_suffix("")
    value = relative.as_posix()
    if value == "index":
        return ""
    return value.removesuffix("/index")


def normalize_mdx(body: str) -> str:
    """Convert supported MDX-only constructs into portable Markdown.

    Returns:
        Markdown with imports removed and code examples expanded from source.
    """
    body = re.sub(r"^import .*?;\n", "", body, flags=re.MULTILINE)
    body = re.sub(r"^\s*</?Steps>\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r'<span id="[^"]+"></span>', "", body)

    def expand_code(match: re.Match[str]) -> str:
        attributes = dict(re.findall(r'(\w+)="([^"]+)"', match.group(0)))
        source = ROOT / attributes["path"]
        code = source.read_text(encoding="utf-8")
        region = attributes.get("region")
        if region:
            start_marker = f"# docs:start {region}"
            end_marker = f"# docs:end {region}"
            start = code.index(start_marker)
            end = code.index(end_marker)
            code = code[code.index("\n", start) + 1 : end]
        block = f"```{attributes.get('lang', 'text')}\n{code.strip()}\n```"
        return "\n".join(match["indent"] + line for line in block.splitlines())

    return re.sub(
        r"(?P<indent>^[ \t]*)<CodeExample\b.*?\s*/>",
        expand_code,
        body,
        flags=re.DOTALL | re.MULTILINE,
    )


def generate_llm_files() -> None:
    """Generate the concise and full-text LLM documentation exports."""
    base = os.environ.get("LYRA_DOCS_BASE", DEFAULT_BASE).rstrip("/")
    pages = navigation_pages()
    index_lines = [
        "# Lyra documentation",
        "",
        "Canonical documentation and generated contracts for Lyra.",
        "",
    ]
    full_lines = ["# Lyra documentation", ""]
    for path in pages:
        title, description, body = read_frontmatter(path)
        slug = page_slug(path)
        url = f"{SITE}{base}/{slug}/" if slug else f"{SITE}{base}/"
        index_lines.append(f"- [{title}]({url}): {description}")
        full_lines.extend([f"# {title}", "", description, "", normalize_mdx(body), ""])
    api_url = f"{SITE}{base}/{PYTHON_API_SLUG}/llms.txt"
    api_link = f"[Python API reference]({api_url})"
    index_lines.append(f"- {api_link}: Complete Python API Markdown export.")
    full_lines.extend(
        [
            "# Python API reference",
            "",
            f"Python API content is published separately: {api_link}.",
            "",
        ]
    )
    write_text(PUBLIC_DIR / "llms.txt", "\n".join(index_lines))
    write_text(PUBLIC_DIR / "llms-full.txt", "\n".join(full_lines))


if __name__ == "__main__":
    main()
