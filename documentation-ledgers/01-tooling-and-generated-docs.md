# Ledger 01: Documentation Tooling, Generated Reference, and Scaffolding

Issues in `docs/scripts/`, `docs/astro.config.mjs`, `docs/public/`, `docs/content-plan.json`, `docs/archive/`, and `src/` docstrings that directly break or bloat the rendered site.

---

## Build, Generator, and API-Doc Bugs

### `[ ] TOOL-01` Every MCP tool description renders as a broken plaintext code block + leaks Python docstrings
- **Locations:**
  - `src/lyra/server/mcp/server.py:251-518` (all 11 `@server.tool` functions)
  - `docs/scripts/generate_docs.py:703`
  - Rendered output: `docs/src/content/docs/reference/generated/mcp.md` & `docs/dist/reference/generated/mcp/index.html`
- **Problem:**
  1. All 11 `@server.tool` functions are nested inside `create_mcp_app` / `_register_retained_tools` with 8-space indented Google-style docstrings. `generate_docs.py:703` writes `contract.description` raw without `inspect.cleandoc()`, so Markdown parses lines 2+ as indented code blocks (`<div class="expressive-code"><pre data-language="plaintext">`).
  2. Every tool leaks Python docstring footers (`Returns:\n    The requested domain response.`), and `lyra_get_job_result` (`server.py:372-373`, `mcp.md:1333-1335`) leaks `Raises:\n    tools.ToolCallError: If mandatory fields exceed the wire budget.`.
  3. `lyra_get_job_result` (`server.py:366`, `mcp.md:1329`) and `SERVER_INSTRUCTIONS` (`server.py:174`) use the retired term `"Fetch the descriptor for complete provenance"`.
- **Fix:**
  - Pass clean, explicit `description="..."` strings (or dedented prose without `Returns:`/`Raises:` and without `"descriptor"`) to `@server.tool(...)` in `src/lyra/server/mcp/server.py` so both live `tools/list` wire payloads and generated docs are clean.
  - Also apply `inspect.cleandoc()` defensively in `docs/scripts/generate_docs.py:703`.

### `[ ] TOOL-02` `reference/generated/mcp.md` is 4,214 lines (100.8 KB — 16.8% of `llms-full.txt`)
- **Locations:** `docs/scripts/generate_docs.py:653-734`, `docs/src/content/docs/reference/generated/mcp.md`, `tests/test_docs_generation.py:231-243`
- **Problem:**
  - Embeds complete pretty-printed JSON Schemas (`inputSchema` and `outputSchema`) inside `<details>` for all 11 tools, repeating massive `$defs` trees (`OutputMetadata`, `ColumnMetadata`, `ProviderScope`, `EffectiveScope`, `Origin`, etc.) up to 4× (`lyra_inspect_report`: 956L, `lyra_get_job_result`: 803L, `lyra_get_metric`: 598L, `lyra_describe_result`: 585L).
  - For the 3 wrapped-union tools (`lyra_get_job_result`, `lyra_describe_result`, `lyra_inspect_report`), the human-readable Output table has only 1 row (`result`), forcing readers to expand 600–950 lines of raw JSON.
- **Fix:**
  - Stop embedding full raw JSON Schemas in `mcp.md`; link directly to `mcp-tools.json` (already published in `docs/public/mcp-tools.json`).
  - Unwrap `result` union variants in `schema_fields()` (already implemented for top-level `anyOf`/`oneOf` at `generate_docs.py:410-421`) so every tool has readable field tables.
  - Update `tests/test_docs_generation.py:231-243` accordingly. Expected size reduction: ~4,214 lines → ~350 lines (~90 KB off `llms-full.txt`).

### `[ ] TOOL-03` `starlight-pydocs` dumps 2,711 internal symbols across 169 pages while hiding public client methods
- **Locations:** `docs/astro.config.mjs:97-127`, `src/lyra/api/client/sync.py:200-534`, `src/lyra/api/client/async_.py:203-547`, `docs/src/content/docs/reference/python.md:18,24,31`
- **Problem:**
  - Exports every internal module in `src/lyra/` (169+ HTML pages, 850 KB `api/lyra/llms.txt`, 570 KB `symbols.json`, now also including `lyra.runtime.terminal_history` and `lyra.server.terminal_jobs` added in `20c90a3b`):
    - `runtime`: 44 pages / 705 symbols (`sqlite_schema`, `sqlite_store`, `input_capture`, `lineage`, `reuse`, `planning/*`, etc.)
    - `server`: 36 pages / 365 symbols (internal FastAPI routes, RQ workers, `response_budget`, `result_projection`, etc.)
    - `contracts`: 38 pages / 1,043 symbols (internal helpers `strict`, `value_identity`, `revision_tokens`, `job_paths`, etc.)
    - `sdk`: 22 pages / 342 symbols (internal `handler`, `loading`, `managed_provider`, `output_schema`, `provider_schema`, `query_schema`, `storage_schema`)
    - `api`: 13 pages / 183 symbols (exposes internal `httpx`, `endpoints`, `polling`, `submission`)
  - Meanwhile, because client resource classes are underscore-prefixed (`_ResultsResource`, `_JobsResource`, `_CatalogResource`, `_ReportsResource`, `_HealthResource`), **every public method on `LyraClient` / `AsyncLyraClient`** (`describe`, `submit`, `run`, `table`, `tables`, `export_geoparquet`, etc.) is completely missing from `api/lyra/api/client/sync/` and `async_/`.
  - `docs/astro.config.mjs:102-112` adds a brittle workaround (`api/client-exports/{sync,async}`) for only 2 GeoParquet methods.
  - `reference/python.md:24` links `Input` to `api/lyra/sdk/dependencies/`, which does not export `Input` (`src/lyra/sdk/dependencies.py:3-5` has `__all__ = ["Dependency", "LocationDependency"]`).
- **Fix:**
  - Scope `starlight-pydocs` strictly to the public Python API surface:
    - `lyra.sdk` (public exports)
    - `lyra.api` (client + admin client + exceptions + options, with resource classes made public or explicitly exposed)
    - `lyra.runtime.local`, `lyra.runtime.results`, `lyra.runtime.result_store`
    - `lyra.data.interventions`
    - Public `lyra.contracts.*` (`errors`, `jobs`, `origins`, `outputs`, `provider_scope`, `reports`, `resources`, `results`, `config`)
    - `lyra.utils` (excluding legacy GeoJSON helpers if retired)
  - Remove the `api/client-exports/{sync,async}` workaround from `docs/astro.config.mjs:102-112`.
  - Re-export `Input` cleanly or point `reference/python.md` to `lyra.sdk` / `lyra.contracts.dependencies`.

### `[ ] TOOL-04` `remark-api-links.mjs` injects wrong links into ordinary inline code
- **Locations:** `docs/scripts/remark-api-links.mjs:13-29`
- **Problem:** Suffix matching across all 2,711 indexed symbols turns unrelated prose tokens into broken internal links in built HTML (`docs/dist/`):
  - `reference/target-selection.md:66`: Ibis’s `` `execute()` `` → `GraphExecutor.execute`
  - `concepts/caching.md`: cache policy literal `` `per_location` `` → `BoundInvocation.per_location`
  - `concepts/provenance.md`: outcome strings `` `executed` `` / `` `reused` `` → `ComputationObservation.executed` / `.reused`; adapter strings `` `parquet` `` / `` `postgis` `` → `lyra.sdk.parquet` / `lyra.sdk.postgis`
  - `reference/parameters.md:19`: argument name `` `context` `` → module `lyra.sdk.context`
  - Multiple files: column name `` `location_id` `` → internal `RecordIdentity.location_id`
- **Fix:**
  - Once `starlight-pydocs` is scoped to public symbols (`TOOL-03`), also tighten `remark-api-links.mjs`: only autolink exact qualified names (`lyra.*` / `Class.method`) or unique public PascalCase class names—never bare lowercase attribute/module suffixes like `context`, `execute`, `parquet`, `location_id`, `executed`.

### `[ ] TOOL-05` `generate_config_reference()` has factual bugs, missing `const: 6`, broken `\|` escapes, and verbose layout
- **Locations:** `docs/scripts/generate_docs.py:451-601`, `docs/src/content/docs/reference/generated/configuration.md`
- **Problems:**
  1. **False merging claim** (`generate_docs.py:555-557`, `configuration.md:11`): *"Lyra does not merge section defaults into a section you supply."* False—supplying partial sections (`jobs={"queue": "custom"}`, `mcp={"enabled": True}`, `api={"public_base_url": "..."}`) **does** apply optional field defaults in `LyraConfig.model_validate`.
  2. **Stale secret-source claim** (`generate_docs.py:550-551`, `configuration.md:9`): *"Database passwords and API credentials come from environment variables."* Resource secrets also accept UTF-8 `file` paths, and core Lyra has no database password env var.
  3. **Missing `schema_version = 6` constraint** (`generate_docs.py:375-386,493`, `configuration.md:86-95`): `schema_constraints()` ignores `"const"` and `"enum"`, so `schema_version: Literal[6]` shows `Constraints: —`.
  4. **Broken literal `\|` in HTML** (`generate_docs.py:582`, `configuration.md:72,226,340`): Passes bullet-list values through `markdown_cell()`, rendering literal `string \| null` in built HTML in 4 places.
  5. **Verbose layout**: Duplicates every top-level heading (`## api` followed immediately by `### api`) and uses 6-line bullet blocks per field instead of compact per-section Markdown tables.
- **Fix:** Fix the intro prose, include `const`/`enum` in `schema_constraints()`, stop escaping `|` outside tables, and render each config section as a clean Markdown table.

### `[ ] TOOL-06` `generate_http_reference()` drops binary Arrow responses and actual error status codes
- **Locations:** `docs/scripts/generate_docs.py:209-324`, `docs/src/content/docs/reference/generated/http.md`, `src/lyra/server/routes/*.py`
- **Problems:**
  1. `generate_docs.py:312-316` checks only `content["application/json"]`, so `GET /results/{job_id}/tables/download` (`http.md:419-437`) and `GET /results/{job_id}/location-inputs/download` (`http.md:399-417`) show `200 | Successful Response | —` with no mention of `application/vnd.apache.arrow.stream`.
  2. OpenAPI route definitions omit actual error responses (`404`/`409`/`500`/`503`) raised in `routes/results.py`, `routes/reports.py`, `routes/metrics.py`, `routes/admin.py`, and `routes/jobs.py`.
  3. `GET /jobs/{job_id}/result/inspection` (`routes/jobs.py:171-172`, `http.md:345,348`) declares `ActiveResultOutput | TerminalResultOutput` for both `200` and `202` instead of `TerminalResultOutput` (`200`) and `ActiveResultOutput` (`202`).
- **Fix:** Inspect all content types in `generate_http_reference()` (showing `application/vnd.apache.arrow.stream`), and declare the real status codes/models on the FastAPI routes so both `/openapi.json` and `http.md` are accurate.

### `[ ] TOOL-07` `generate_cli_reference()` formatting and missing argparse help
- **Locations:** `docs/scripts/generate_docs.py:604-650`, `docs/src/content/docs/reference/generated/cli.md`, `src/lyra/sdk/plugin_cli.py`, `src/lyra/api/admin_cli.py`
- **Problems:**
  - Renders ``## `worker launcher` `` in backticks as if it were a shell command (`generate_docs.py:609,634`); actual invocation is `python -m lyra.server.worker_launcher`.
  - Flattens 22 `##` headings and emits generic `"Inspect help for ..."` fallback text on 8 subcommands whose argparse parsers lack `description=`.
  - Several CLI options lack `help=` strings (`--project`, `--include-project`, `--output` on `lyra-plugin diagram`; `--limit`, `--status`, `--offset`, `id`, `name`, `PATH` on `lyra-admin`).
- **Fix:** Use actual command `python -m lyra.server.worker_launcher`, group subcommands under `lyra-plugin` / `lyra-admin` / worker launcher, and add missing `description=` and `help=` text in the parsers.

### `[ ] TOOL-08` Static MCP example JSON files fail Pydantic validation
- **Locations:**
  - `docs/public/examples/mcp/multiple-outputs.json:43-47`
  - `docs/public/examples/mcp/compact-provenance.json:43-47,89-105`
- **Problems:**
  - Commit `20c90a3b` removed the deleted `"lifetime"` field from these files, but both **still** fail Pydantic validation:
  - Both declare `"geometry": "geometry"` with `"columns": []`, raising `ValueError: Geometry must identify the single declared geometry column` in `OutputMetadata.validate_columns` (`src/lyra/contracts/outputs.py:64-69`).
  - Both omit `"truncation"` emitted by `TerminalResultOutput.model_dump(mode="json")` (`src/lyra/contracts/inspection.py:72`).
  - `compact-provenance.json` includes `"provenance"` without `"truncation": {"omitted_sections": [...]}` (`src/lyra/server/result_projection.py:174-176`).
- **Fix:** Regenerate all `docs/public/examples/mcp/*.json` files from real `InspectionOutput` / `project_observation()` calls and validate them against Pydantic models in `tests/test_docs_contract.py`.

---

## Scaffolding & Debris to Delete

### `[ ] TOOL-09` Delete `docs/archive/lyra-tui-changelog.md`
- **Location:** `docs/archive/lyra-tui-changelog.md`
- **Why:** Historical changelog for a removed TUI package; irrelevant in closed alpha.

### `[ ] TOOL-10` Remove `docs/content-plan.json` and its test gate
- **Locations:**
  - `docs/content-plan.json` (1,050 lines)
  - `docs/src/content.config.ts:12-16` (`contentPlan` field)
  - `docs/scripts/generate_docs.py:182-195` (`content_plan` arg)
  - `tests/test_docs_contract.py:109-123` (`test_editorial_plans_cover_all_published_guides`)
  - `contentPlan:` frontmatter across all 66 authored pages + `docs/src/pages/404.astro:9`
- **Why:** High-maintenance mirror of headings that adds friction to every edit without preventing factual drift or bad prose. (Optionally keep `contentType` if useful for editorial review, or drop both.)

### `[ ] TOOL-11` Strip all 25 legacy `<span id="...">` anchor hacks
- **Locations:**
  - `docs/src/content/docs/quickstart.mdx:23,34,49,77,90,109` (6 headings, including 3 spans on line 23 with a trailing-hyphen typo `set-your-connection-details-`)
  - `docs/src/content/docs/operate/deployment.mdx:17,34,45,55,69`
  - `docs/src/content/docs/operate/plugins.mdx:15,28,41`
  - `docs/src/content/docs/plugins/agent-assisted-authoring.mdx:17,43,53,76,88,98`
  - `docs/src/content/docs/plugins/publish-and-debug.mdx:15,51`
  - `docs/src/content/docs/plugins/dependencies.mdx:86,112`
  - `docs/src/content/docs/plugins/tutorial/testing.md:132` (creates broken trailing-hyphen slug `test-a-data-loading-failure-`)
- **Fix:** Remove all `<span id="..."></span>` tags, update any internal links to the natural heading slugs, and remove the `<span id=` strip regex from `generate_docs.py:854` (and its assertion in `tests/test_docs_contract.py:133,144`).
