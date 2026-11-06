"""Generate all source-derived Lyra documentation and machine contracts."""

from __future__ import annotations

import json
import os
import re
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, get_args, get_origin
from urllib.parse import urljoin

from fastapi import FastAPI
from filelock import FileLock
from marko import Markdown
from marko.block import LinkRefDef
from marko.inline import Image, Link
from marko.md_renderer import MarkdownRenderer
from pydantic import BaseModel, TypeAdapter

from lyra.api.admin_cli import build_parsers as build_admin_parsers
from lyra.contracts.config import LyraConfig
from lyra.sdk.plugin_cli import build_parsers as build_plugin_parsers
from lyra.server.mcp.models import TOOL_CONTRACTS
from lyra.server.mcp.server import SERVER_INSTRUCTIONS
from lyra.server.routes import admin, data_types, health, jobs, metrics
from lyra.server.worker_launcher import build_parser as build_worker_parser
from lyra.version import APP_VERSION

if TYPE_CHECKING:
    from marko.element import Element

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
CONTENT = Path("src/content/docs")
GENERATED = Path("reference/generated")
PUBLIC_FILES = (
    "openapi.json",
    "config.schema.json",
    "mcp-tools.json",
    "llms.txt",
    "llms-full.txt",
)
CONTENT_DIR = DOCS_DIR / CONTENT
GENERATED_DIR = CONTENT_DIR / GENERATED
PUBLIC_DIR = DOCS_DIR / "public"
SITE = "https://rodolfofigueroa.github.io"
DEFAULT_BASE = "/Lyra/latest"
PYTHON_API_SLUG = "api/lyra"

ENV_FIELDS = {
    "LYRA_ADMIN_API_KEY": ("API", "Administrator Bearer key"),
    "LYRA_AGENT_API_KEY": ("API", "Agent Bearer key, required even with MCP disabled"),
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


def checked_path(root: Path, relative: Path) -> Path:
    """Return a path below root, rejecting traversal and symlink components.

    Raises:
        DocumentationGenerationError: If the path crosses the ownership boundary.
    """
    if relative.is_absolute() or ".." in relative.parts or root.is_symlink():
        message = f"Unsafe documentation path: {root} / {relative}"
        raise DocumentationGenerationError(message)
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            message = f"Symlink in documentation path: {path}"
            raise DocumentationGenerationError(message)
    return path


def main(docs_dir: Path = DOCS_DIR) -> None:
    """Stage a complete generation before replacing any published output."""
    lock = checked_path(docs_dir, Path(".generate-docs.lock"))
    with (
        FileLock(lock),
        TemporaryDirectory(dir=docs_dir, prefix=".generate-docs-") as temp,
    ):
        stage = Path(temp)
        generated = checked_path(stage, CONTENT / GENERATED)
        public = stage / "public"
        generate_contracts(generated, public)
        generate_llm_files(docs_dir, generated, public)
        publish_outputs(stage, docs_dir)


def publish_outputs(stage: Path, docs_dir: Path) -> None:
    """Replace the owned tree and named files, restoring backups on an I/O error.

    Raises:
        DocumentationGenerationError: If a public output is missing or unexpected,
            or a staged or live output has an unsafe type.
    """
    public = checked_path(stage, Path("public"))
    if {path.name for path in public.iterdir()} != set(PUBLIC_FILES):
        message = "Staged public files differ from the documentation output inventory"
        raise DocumentationGenerationError(message)

    reference = CONTENT / GENERATED
    outputs = [reference, *(Path("public") / name for name in PUBLIC_FILES)]
    for relative in outputs:
        source = checked_path(stage, relative)
        target = checked_path(docs_dir, relative)
        expected_type = Path.is_dir if relative == reference else Path.is_file
        if not expected_type(source) or (target.exists() and not expected_type(target)):
            message = f"Unexpected documentation output type: {relative}"
            raise DocumentationGenerationError(message)

    staged_reference = stage / reference
    for entry in staged_reference.rglob("*"):
        if entry.is_symlink():
            message = f"Symlink in staged documentation: {entry}"
            raise DocumentationGenerationError(message)

    # Moving the old tree as a unit also removes stale nested files. Temporary
    # directory cleanup unlinks symlinks inside that tree without following them.
    backup = stage / "previous"
    with ExitStack() as rollback:
        for relative in outputs:
            source = stage / relative
            target = docs_dir / relative
            previous = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                previous.parent.mkdir(parents=True, exist_ok=True)
                target.replace(previous)
                rollback.callback(previous.replace, target)
            source.replace(target)
            rollback.callback(target.replace, source)
        rollback.pop_all()


def generate_contracts(generated: Path, public: Path) -> None:
    """Render the reference pages and their machine-readable contracts together."""
    openapi = generate_http_reference(generated)
    config_schema = generate_config_reference(generated)
    generate_cli_reference(generated)
    generate_mcp_reference(generated)
    write_json(public / "openapi.json", openapi)
    write_json(public / "config.schema.json", config_schema)
    write_json(
        public / "mcp-tools.json",
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
    ):
        app.include_router(router)
    return app


def generate_http_reference(generated: Path) -> dict[str, Any]:
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
    write_text(generated / "http.md", "\n".join(lines))
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
    model: type[BaseModel] = LyraConfig,
    prefix: str = "",
) -> list[ConfigRow]:
    """Describe fields and section defaults from their owning configuration model.

    Requiredness is relative to a supplied parent section. Model defaults apply
    when the entire field is omitted, never as a merge into a supplied section.

    Returns:
        Section and field rows in declaration order.

    Raises:
        DocumentationGenerationError: If a field has no description.
    """
    properties = resolve_schema(schema, root).get("properties", {})
    rows: list[ConfigRow] = []
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        raw = properties[name]
        description = field.description
        if not description:
            message = f"Config field lacks a description: {path}"
            raise DocumentationGenerationError(message)
        default = "—"
        if not field.is_required():
            value = field.get_default(call_default_factory=True)
            default = json.dumps(
                TypeAdapter(
                    field.annotation if field.annotation is not None else Any
                ).dump_python(value, mode="json"),
                sort_keys=True,
            )
        rows.append(
            ConfigRow(
                path=path,
                type_name=schema_type(raw),
                required=field.is_required(),
                default=default,
                constraints=schema_constraints(raw) or "—",
                description=description,
            )
        )
        annotation = field.annotation
        candidates = (
            [annotation]
            if get_origin(annotation) in {dict, list}
            else [annotation, *get_args(annotation)]
        )
        nested_model = next(
            (
                item
                for item in candidates
                if isinstance(item, type) and issubclass(item, BaseModel)
            ),
            None,
        )
        if nested_model is not None:
            nested_schema = next(
                (branch for branch in raw.get("anyOf", []) if "$ref" in branch), raw
            )
            rows.extend(
                config_rows(nested_schema, root=root, model=nested_model, prefix=path)
            )
        elif get_origin(annotation) in {dict, list}:
            is_mapping = get_origin(annotation) is dict
            value_type = get_args(annotation)[1 if is_mapping else 0]
            if isinstance(value_type, type) and issubclass(value_type, BaseModel):
                rows.extend(
                    config_rows(
                        raw["additionalProperties" if is_mapping else "items"],
                        root=root,
                        model=value_type,
                        prefix=f"{path}.<name>" if is_mapping else f"{path}[]",
                    )
                )
    return rows


def generate_config_reference(generated: Path) -> dict[str, Any]:
    """Generate the TOML and environment configuration reference.

    Returns:
        The complete JSON Schema generated from ``LyraConfig``.

    """
    schema = LyraConfig.model_json_schema()
    rows = config_rows(schema, root=schema)
    toml_rows = rows
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
            "When you include a section, supply every field marked required. "
            "If you omit the entire section, Lyra uses its default values. "
            "Lyra does not merge section defaults into a section you supply."
        ),
        "",
        "In field paths, `[]` identifies an entry in an array of TOML tables.",
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
            "| Variable | Required by | Secret | Description |",
            "| --- | --- | --- | --- |",
        ]
    )
    for variable, (roles, description) in ENV_FIELDS.items():
        lines.append(f"| `{variable}` | {roles} | yes | {description} |")
    write_text(generated / "configuration.md", "\n".join(lines))
    return schema


def generate_cli_reference(generated: Path) -> None:
    """Generate formatted help for every supported command-line interface."""
    parsers = (
        *((parser.prog, parser) for parser in build_plugin_parsers()),
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
    write_text(generated / "cli.md", "\n".join(lines))


def generate_mcp_reference(generated: Path) -> None:
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
    write_text(generated / "mcp.md", "\n".join(lines))


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
        message = f"Missing frontmatter: {path}"
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
        message = f"Incomplete frontmatter: {path}"
        raise DocumentationGenerationError(message) from exc


def navigation_slugs(items: list[str | dict[str, Any]]) -> list[str]:
    """Return page slugs in reading order from nested sidebar groups."""
    slugs: list[str] = []
    for item in items:
        if isinstance(item, str):
            slugs.append(item)
        else:
            slugs.extend(navigation_slugs(item["items"]))
    return slugs


def navigation_pages(
    docs_dir: Path = DOCS_DIR, *, generated_dir: Path | None = None
) -> list[Path]:
    """Resolve navigation entries to their source documentation pages.

    Returns:
        Pages in navigation order, with generated sections expanded by filename.

    Raises:
        DocumentationGenerationError: If a configured page does not exist.
    """
    content = checked_path(docs_dir, CONTENT)
    if generated_dir is None:
        generated_dir = checked_path(content, GENERATED)
    navigation = json.loads(
        checked_path(docs_dir, Path("navigation.json")).read_text(encoding="utf-8")
    )
    pages: list[Path] = []
    for slug in navigation_slugs(navigation):
        if slug == PYTHON_API_SLUG:
            continue
        relative = Path(slug)
        if relative == GENERATED:
            pages.extend(
                checked_path(generated_dir, path.relative_to(generated_dir))
                for path in sorted(generated_dir.rglob("*.md"))
            )
            continue
        root = content
        if relative.is_relative_to(GENERATED):
            root = generated_dir
            relative = relative.relative_to(GENERATED)
        candidates = (
            checked_path(root, Path(f"{relative}.md")),
            checked_path(root, Path(f"{relative}.mdx")),
        )
        page = next(
            (candidate for candidate in candidates if candidate.is_file()), None
        )
        if page is None:
            message = f"Navigation page does not exist: {slug}"
            raise DocumentationGenerationError(message)
        pages.append(page)
    return pages


def page_slug(relative: Path) -> str:
    """Convert a documentation source path to its site-relative URL slug.

    Returns:
        The normalized slug, or an empty string for the site index.
    """
    value = relative.with_suffix("").as_posix()
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
    # The surrounding prose supplies the diagrams' meaning in text exports.
    body = re.sub(
        r"^[ \t]*<(?:MaintenanceDependenciesDiagram|BlockWorkloadDiagram)\s*/>[ \t]*$",
        "",
        body,
        flags=re.MULTILINE,
    )
    body = re.sub(r'<span id="[^"]+"></span>', "", body)

    def expand_code(match: re.Match[str]) -> str:
        attributes = dict(re.findall(r'(\w+)="([^"]+)"', match.group(0)))
        source = checked_path(ROOT, Path(attributes["path"]))
        code = source.read_text(encoding="utf-8")
        region = attributes.get("region")
        if region:
            start_marker = f"# docs:start {region}"
            end_marker = f"# docs:end {region}"
            start = code.index(start_marker)
            end = code.index(end_marker)
            code = code[code.index("\n", start) + 1 : end]
        title = attributes.get("title")
        metadata = f' title="{title}"' if title else ""
        block = f"```{attributes.get('lang', 'text')}{metadata}\n{code.strip()}\n```"
        return "\n".join(match["indent"] + line for line in block.splitlines())

    return re.sub(
        r"(?P<indent>^[ \t]*)<CodeExample\b.*?\s*/>",
        expand_code,
        body,
        flags=re.DOTALL | re.MULTILINE,
    )


def portable_markdown(body: str, page_url: str) -> str:
    """Resolve Markdown links against their source page for concatenated exports.

    Returns:
        Markdown with absolute link and image targets, preserving code contents.
        Reference links become inline links so page-local labels cannot collide.
    """
    markdown = Markdown(renderer=MarkdownRenderer, extensions=["gfm"])
    document = markdown.parse(normalize_mdx(body))
    document.link_ref_defs.clear()
    pending: list[Element] = [document]
    while pending:
        element = pending.pop()
        if isinstance(element, (Link, Image)):
            element.dest = urljoin(page_url, element.dest)
        children = getattr(element, "children", None)
        if isinstance(children, list):
            children[:] = [
                child for child in children if not isinstance(child, LinkRefDef)
            ]
            pending.extend(children)
    return markdown.render(document)


def generate_llm_files(docs_dir: Path, generated: Path, public: Path) -> None:
    """Generate the concise and full-text LLM documentation exports."""
    base = os.environ.get("LYRA_DOCS_BASE", DEFAULT_BASE).rstrip("/")
    pages = navigation_pages(docs_dir, generated_dir=generated)
    index_lines = [
        "# Lyra documentation",
        "",
        "Canonical documentation and generated contracts for Lyra.",
        "",
    ]
    full_lines = ["# Lyra documentation", ""]
    for path in pages:
        title, description, body = read_frontmatter(path)
        relative = (
            GENERATED / path.relative_to(generated)
            if path.is_relative_to(generated)
            else path.relative_to(docs_dir / CONTENT)
        )
        slug = page_slug(relative)
        url = f"{SITE}{base}/{slug}/" if slug else f"{SITE}{base}/"
        index_lines.append(f"- [{title}]({url}): {description}")
        full_lines.extend(
            [
                f"# {title}",
                "",
                f"Source: [{title}]({url})",
                "",
                description,
                "",
                portable_markdown(body, url),
                "",
            ]
        )
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
    write_text(public / "llms.txt", "\n".join(index_lines))
    write_text(public / "llms-full.txt", "\n".join(full_lines))


if __name__ == "__main__":
    main()
