# Phase 1: Backend Contracts, Doc Tooling, & Dead Scaffolding

**Goal:** Fix every broken generated reference page, eliminate internal-symbol pollution and broken autolinks, land all small `src/` / Docker contract fixes that docs depend on, wire up Mermaid diagram support, and delete dead documentation scaffolding—leaving a clean foundation before moving or rewriting authored pages.

**Covered ledger IDs:**
- Tooling & scaffolding: `TOOL-01` through `TOOL-11`
- Code/infra-side fixes: `FACT-03` (code docstring), `FACT-13` (retire `lyra.utils.geometry`), `FACT-20` (`stdout`), `FACT-25` (`/lyra_data/artifacts`), `FACT-35` (`lyra[client]`), `FACT-41` (Compose `/ready` healthcheck), `UX-16` (retire `docker/docker-compose.yml`), `UX-22` (Mermaid plugin)
- Style/generator alignment: `STYLE-08` (sentence-case generated sidebar labels)

Split Phase 1 into **two separate commits/PRs** (**Batch 1A** and **Batch 1B**) so runtime/package changes stay cleanly isolated from docs-pipeline changes.

---

## Batch 1A — Application Contracts, CLI Parsers, Public Client Resources, & Deployment Cleanup

### 1. Clean MCP tool descriptions & server instructions (`TOOL-01`)
- **Files:** `src/lyra/server/mcp/server.py`
- **Changes:**
  - [ ] Pass clean, explicit `description="..."` prose (or dedented descriptions with no `Returns:`/`Raises:` blocks) on all 11 `@server.tool` registrations (`server.py:251-518`) so both live `tools/list` wire payloads and generated docs render clean prose without indented plaintext code blocks.
  - [ ] Remove the retired term `"descriptor"` from `lyra_get_job_result` (`server.py:366`) and `SERVER_INSTRUCTIONS` (`server.py:174`); use `"description"` / `"saved result description"`.
- **Tests:** Run MCP server tests (`tests/test_mcp_server.py`) and update any exact-description expectations if behavioral assertions exist.

### 2. Fix OpenAPI route error statuses & inspection 200/202 split (`TOOL-06`)
- **Files:** `src/lyra/server/routes/jobs.py`, `results.py`, `reports.py`, `metrics.py`, `admin.py`
- **Changes:**
  - [ ] On `GET /jobs/{job_id}/result/inspection` (`routes/jobs.py:171-172`), declare `TerminalResultOutput` for HTTP `200` and `ActiveResultOutput` for HTTP `202` (instead of `ActiveResultOutput | TerminalResultOutput` on both).
  - [ ] Declare the real HTTP error status responses (`404`, `409`, `500`, `503`) raised by routes in `results.py`, `reports.py`, `metrics.py`, `admin.py`, and `jobs.py` so both `/openapi.json` and `reference/generated/http.md` match runtime behavior.
- **Tests:** Run HTTP route/boundary tests (e.g., `tests/test_http_job_boundaries.py`, `tests/test_jobs_route.py`, `tests/test_docs_contract.py -k http`).

### 3. Expose `lyra.server.main.build_parser()` & complete CLI argparse help (`TOOL-07`)
- **Files:** `src/lyra/server/main.py`, `src/lyra/server/worker_launcher.py`, `src/lyra/sdk/plugin_cli.py`, `src/lyra/api/admin_cli.py`
- **Changes:**
  - [ ] Extract a top-level `build_parser() -> argparse.ArgumentParser` in `src/lyra/server/main.py` (currently inline under `if __name__ == "__main__":` at `L117-120`) with a clear `description=` and `--config` `help=` string.
  - [ ] Add missing `description=` on all 9 subcommand parsers that currently fall back to `"Inspect help for ..."`, including `lyra-plugin dev` (`plugin_cli.py:283-285`, documenting its default private `valkey-server` broker and `--valkey-url`).
  - [ ] Add missing option `help=` strings: `--project` on `lyra-plugin dev`; `--config` on `worker_launcher`; `--project`, `--include-project`, `--output` on `lyra-plugin diagram`; `--limit`, `--status`, `--offset`, `id`, `name`, `PATH` on `lyra-admin`.

### 4. Make Python client resource classes public (`TOOL-03`)
- **Files:** `src/lyra/api/client/sync.py`, `src/lyra/api/client/async_.py`, `src/lyra/api/client/__init__.py` (and `src/lyra/api/__init__.py` if appropriate)
- **Changes:**
  - [ ] Rename the underscore-prefixed resource classes in `sync.py` (`L200-534`) and `async_.py` (`L203-547`) to public names (e.g., `HealthResource`, `CatalogResource`, `ReportsResource`, `ResultsResource`, `JobsResource`, `AdminJobsResource`, `AdminPluginsResource`, `AdminCatalogResource`, `AdminWorkersResource`, `AdminQueueResource` in `sync.py`, and corresponding `Async*Resource` or module-scoped public names in `async_.py`) and export them in `__all__`.
  - [ ] Ensure `LyraClient` / `AsyncLyraClient` / `LyraAdminClient` / `AsyncLyraAdminClient` attribute type annotations reference the public resource classes so `starlight-pydocs` links directly from client attributes to every resource method (`describe`, `submit`, `run`, `table`, `tables`, `export_geoparquet`, etc.).
- **Tests:** Run client test suites (`tests/test_api_client*.py`, `tests/test_public_imports.py`, `tests/test_client_imports.py`).

### 5. Switch server console logging to `stdout` (`FACT-20`) & fix stale data-types docstring (`FACT-03`)
- **Files:** `src/lyra/server/logging_config.py`, `src/lyra/contracts/data_types.py`
- **Changes:**
  - [ ] In `src/lyra/server/logging_config.py:36-38`, pass `sys.stdout` to `logging.StreamHandler(sys.stdout)` when `log_file is None`. Update any logging unit test that captures `stderr` instead of `stdout`.
  - [ ] In `src/lyra/contracts/data_types.py:13`, replace `"Select opaque IDs by collection and unit type."` with `"Select location records by provider ID and record IDs."`

### 6. Retire legacy GeoJSON modules completely (`TOOL-03`, `FACT-13`)
- **Files to delete:**
  - [ ] `src/lyra/utils/geometry.py`
  - [ ] `src/lyra/contracts/geometry.py`
  - [ ] `tests/test_geometry.py`
- **Files to update:**
  - [ ] `src/lyra/utils/__init__.py`: update module docstring (`"Shared utilities; import helpers from the date or ee modules."`).
  - [ ] `tests/test_metric_skill_earth_engine.py:28,179,256`: replace `convert_geojson_to_gdf(...)` helper calls with direct `gpd.GeoDataFrame` construction.
  - [ ] Remove retired import checks from `tests/test_public_imports.py:120,139`, `tests/test_installation_profiles.py:101`, and `tests/test_contract_imports.py:63`.
  - [ ] Verify no remaining imports of `lyra.utils.geometry` or `lyra.contracts.geometry` exist anywhere in `src/`, `tests/`, or `examples/`.

### 7. Reconcile Docker/Compose deployment stack (`UX-16`, `FACT-25`, `FACT-35`, `FACT-41`, `UX-18`)
- **Files:** `docker/docker-compose.yml` (delete), `examples/lyra-deployment/{compose.yml,Dockerfile,pyproject.toml,uv.lock}`, `config.example.toml`, `tests/test_docker_filesystem_layout.py`, `.github/workflows/benchmarks.yml`
- **Changes:**
  - [ ] Delete `docker/docker-compose.yml` (and remove the empty `docker/` directory; drop `'docker/**'` from `.github/workflows/benchmarks.yml` if unused).
  - [ ] In `examples/lyra-deployment/compose.yml:25`, strengthen the `api` `/ready` healthcheck to parse the JSON body and exit nonzero unless `status == "ready"` (failing during a `degraded` Valkey outage rather than passing on HTTP `200`).
  - [ ] In `examples/lyra-deployment/Dockerfile`: remove unused `/lyra_data/artifacts` from `mkdir -p` (`L26`) and add `libpq5` in the runtime stage (`L20-22`) for PostGIS parity with root `Dockerfile`.
  - [ ] In `examples/lyra-deployment/pyproject.toml`: change `"lyra[server]"` to `"lyra[server,client]"` (and refresh `examples/lyra-deployment/uv.lock`) so `docker compose exec api lyra-admin` has an explicit `httpx` dependency (`FACT-35`).
  - [ ] In `config.example.toml`: remove the misleading `LYRA_DATA_DIR` comment (`L8`, `FACT-23`), write `/lyra_data/results.sqlite3`, and change `url = "redis://lyra-valkey:6379/0"` (`L24`) to `url = "redis://valkey:6379/0"` to match `examples/lyra-deployment/compose.yml`.
  - [ ] Update `tests/test_docker_filesystem_layout.py` to validate `examples/lyra-deployment/compose.yml` and both Dockerfiles (`Dockerfile` and `examples/lyra-deployment/Dockerfile`), including strict `/ready` healthcheck and `stop_grace_period` coverage.

### Batch 1A Verification Gate
Run focused tests (escalate if asyncio wakeups are triggered):
```sh
uv run pytest -n auto \
  tests/test_docker_filesystem_layout.py \
  tests/test_example_config.py \
  tests/test_startup_configuration.py \
  tests/test_public_imports.py \
  tests/test_contract_imports.py \
  tests/test_installation_profiles.py \
  tests/test_client_imports.py \
  tests/test_metric_skill_earth_engine.py \
  tests/test_http_job_boundaries.py \
  tests/test_mcp_server.py
```

---

## Batch 1B — Docs Generators, `starlight-pydocs`, `remark-api-links`, Mermaid Plugin, & Dead Scaffolding

### 1. Fix `docs/scripts/generate_docs.py` (`TOOL-01`, `TOOL-02`, `TOOL-05`, `TOOL-06`, `TOOL-07`, `TOOL-10`, `TOOL-11`, `STYLE-08`)
- **Files:** `docs/scripts/generate_docs.py`, `tests/test_docs_generation.py`, `tests/test_docs_contract.py`
- **Changes:**
  - [ ] **Frontmatter (`L182-195`):** Remove `contentPlan` and `contentType` from `frontmatter()`; stop applying Python `str.title()` to `sidebar.label` (`STYLE-08` — emit clean sentence case matching the page title).
  - [ ] **MCP generator (`L653-734`, `TOOL-01`, `TOOL-02`):**
    - Apply `inspect.cleandoc()` defensively to `contract.description`.
    - Stop embedding raw pretty-printed JSON Schemas (`inputSchema` / `outputSchema`) inside `<details>` blocks; link directly to `mcp-tools.json`.
    - Unwrap `result` union variants in `schema_fields()` so `lyra_get_job_result`, `lyra_describe_result`, and `lyra_inspect_report` produce complete readable field tables instead of a single `result` row.
    - Update `tests/test_docs_generation.py:231-243` to verify generated field coverage and `mcp-tools.json` parity without asserting embedded raw JSON blocks.
  - [ ] **Configuration generator (`L375-386`, `L451-603`, `TOOL-05`):**
    - Fix the false merging prose (`L557-559`): state accurately that supplying a partial section merges optional field defaults from `LyraConfig`.
    - Include `"const"` and `"enum"` in `schema_constraints()` so `schema_version` displays `const=8`.
    - Stop passing non-table text through `markdown_cell()` so HTML no longer renders literal `string \| null` (`L584`).
    - Eliminate duplicate top-level headings (`## api` / `### api`) and render each configuration section as a compact Markdown table instead of 6-line bullet blocks.
  - [ ] **HTTP generator (`L209-324`, `TOOL-06`):**
    - Inspect all response content types—not just `application/json`—so Arrow download endpoints show `application/vnd.apache.arrow.stream`.
    - Render the newly declared error status responses (`404`, `409`, `500`, `503`) and split `200`/`202` models.
  - [ ] **CLI generator (`L604-652`, `TOOL-07`):**
    - Include `lyra.server.main.build_parser()` alongside `lyra.server.worker_launcher.build_parser()`.
    - Use real invocation headings (`python -m lyra.server.main`, `python -m lyra.server.worker_launcher`, never ``## `worker launcher` ``).
    - Group subcommands cleanly under `lyra-plugin`, `lyra-admin`, and server launchers (`##` for entry points, `###` for subcommands).
  - [ ] **MDX normalization (`L856`, `TOOL-11`):** Remove the legacy `<span id="...">` strip regex (and update `test_mdx_exports_preserve_nested_examples_without_component_markup` in `tests/test_docs_contract.py:133,144`). *(Leave diagram component strip regexes until Batch 3C deletes the `.astro` diagram components, or remove them if unused.)*

### 2. Scope `starlight-pydocs` to the public API & remove workaround (`TOOL-03`)
- **Files:** `docs/astro.config.mjs`, `docs/src/content/docs/reference/python.md`
- **Changes:**
  - [ ] Remove the `api/client-exports/{sync,async}` package hack (`astro.config.mjs:102-112`).
  - [ ] Configure `starlight-pydocs` to document **only** the public Python API surface:
    - `lyra.sdk` (public exports via `__all__`, excluding internal implementation modules such as `handler`, `loading`, `managed_provider`, `output_schema`, `provider_schema`, `query_schema`, `storage_schema`, `dev`)
    - `lyra.api` (clients, public resource classes, admin clients, exceptions, options; excluding internal `httpx`, `endpoints`, `polling`, `submission`)
    - `lyra.runtime.local`, `lyra.runtime.results`, `lyra.runtime.result_store` (excluding internal SQLite/planning/lineage/reuse modules)
    - `lyra.data.interventions`
    - Public `lyra.contracts.*`: `errors`, `jobs`, `origins`, `outputs`, `provider_scope`, `reports`, `resources`, `results`, `config` (excluding internal helpers like `strict`, `value_identity`, `revision_tokens`, `job_paths`)
    - `lyra.utils.date`, `lyra.utils.ee` (note `lyra.utils.geometry` is retired in Batch 1A)
  - [ ] Fix `docs/src/content/docs/reference/python.md:24` so `Input` links to `api/lyra/sdk/` (since `lyra.sdk.__all__` already exports `Input`) rather than `api/lyra/sdk/dependencies/`.
  - [ ] Verify `docs/scripts/check_site.py` (`api_reference_failures`) passes cleanly on the scoped `api/lyra/symbols.json`.

### 3. Restrict `remark-api-links.mjs` autolinking (`TOOL-04`)
- **Files:** `docs/scripts/remark-api-links.mjs`, `docs/scripts/remark-api-links.test.mjs`
- **Changes:**
  - [ ] Update `symbolLookup()` (`remark-api-links.mjs:12-29`) so it only autolinks:
    1. Exact qualified names (`lyra.*` or `Class.method` / `module.Class`), or
    2. Unique public **PascalCase** class/exception names (e.g., `LyraClient`, `LocalRunner`, `LocationProvider`).
  - [ ] Never match bare lowercase attribute, method, or module suffixes (`context`, `execute`, `parquet`, `postgis`, `location_id`, `executed`, `reused`, `per_location`).
  - [ ] Add unit tests in `docs/scripts/remark-api-links.test.mjs` asserting bare lowercase tokens (even when unique in the registry) do **not** autolink, while qualified names and unique PascalCase public classes still do.

### 4. Add Mermaid plugin to the Astro/Starlight build (`UX-22`)
- **Files:** `docs/package.json`, `docs/package-lock.json`, `docs/astro.config.mjs`
- **Changes:**
  - [ ] Install and configure a Starlight/Astro-compatible Mermaid rendering plugin (e.g., `astro-mermaid` or `rehype-mermaid`—prefer a build-time or client-clean integration that works cleanly in `astro build`, `astro check`, and `check_site.py` without headless-browser flakiness in CI).
  - [ ] Verify `generate_docs.py`'s `portable_markdown()` / `normalize_mdx()` preserves fenced ` ```mermaid ` blocks verbatim in `llms-full.txt`.
  - [ ] Add a quick smoke check during `npm run ci` (verified fully in Phase 3 when the 5 Mermaid diagrams are authored).

### 5. Regenerate & Pydantic-validate `docs/public/examples/mcp/*.json` (`TOOL-08`)
- **Files:** `docs/public/examples/mcp/multiple-outputs.json`, `compact-provenance.json`, `table-result.json`, `tests/test_docs_contract.py`
- **Changes:**
  - [ ] Regenerate all three static MCP JSON example files from real `InspectionOutput` / `project_observation()` / `TerminalResultOutput.model_dump(mode="json")` calls so they include valid geometry columns, `"truncation"`, and accurate `"omitted_sections"`.
  - [ ] Add strict Pydantic validation (`OutputMetadata` / `TerminalResultOutput` / `DownloadResultOutput` / `InspectionOutput`) for `docs/public/examples/mcp/*.json` in `tests/test_docs_contract.py` (in addition to JSON Schema checks).

### 6. Delete dead scaffolding & strip all 29 legacy `<span id="...">` anchors (`TOOL-09`, `TOOL-10`, `TOOL-11`)
- **Files to delete:**
  - [ ] `docs/archive/lyra-tui-changelog.md` (and empty `docs/archive/`) (`TOOL-09`)
  - [ ] `docs/content-plan.json` (`TOOL-10`)
- **Files to update (`TOOL-10`):**
  - [ ] `docs/src/content.config.ts`: remove `contentType` and `contentPlan` from the `docs` collection schema extension.
  - [ ] `docs/src/pages/404.astro:9`: remove `contentPlan` / `contentType` if present.
  - [ ] Strip `contentPlan:` and `contentType:` lines from frontmatter across all 66 authored pages in `docs/src/content/docs/`.
  - [ ] Delete `test_editorial_plans_cover_all_published_guides` from `tests/test_docs_contract.py:109-123`.
- **Files to update (`TOOL-11`):**
  - [ ] Remove all 29 `<span id="..."></span>` tags across:
    - `quickstart.mdx` (`L23,34,49,77,90,109`)
    - `operate/deployment.mdx` (`L17,34,45,55,67`)
    - `operate/plugins.mdx` (`L15,25,38,60`)
    - `operate/updates.mdx` (`L29,59`)
    - `plugins/development.mdx` (`L13`)
    - `plugins/agent-assisted-authoring.mdx` (`L17,43,53,76,88,98`)
    - `plugins/publish-and-debug.mdx` (`L15,51`)
    - `plugins/dependencies.mdx` (`L86,112`)
    - `plugins/tutorial/testing.md` (`L132`)
  - [ ] Update any internal cross-page links pointing to those legacy IDs (e.g., `operate/runbook.md:36` `../updates/#drain-and-restart`, `plugins/development.mdx:26` `../../quickstart/#set-your-connection-details`) to their natural heading slugs.

### Batch 1B Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_generation.py tests/test_docs_contract.py tests/test_documented_parameters.py
```

---

## Phase 1 Acceptance Criteria

- [ ] `docs/src/content/docs/reference/generated/mcp.md` drops from ~4,214 lines to ~350 lines with zero broken plaintext code blocks, zero `Returns:`/`Raises:` leaks, and unwrapped output tables for all 3 union tools.
- [ ] `docs/src/content/docs/reference/generated/configuration.md` renders clean tables, shows `const=8` on `schema_version`, has accurate merging prose, and contains zero literal `\|` in built HTML.
- [ ] `docs/src/content/docs/reference/generated/http.md` documents `application/vnd.apache.arrow.stream`, real error status codes, and split `200`/`202` models on `/jobs/{job_id}/result/inspection`.
- [ ] `docs/src/content/docs/reference/generated/cli.md` includes `python -m lyra.server.main`, uses real command headings, and has zero `"Inspect help for ..."` fallbacks.
- [ ] `docs/dist/api/lyra/symbols.json` contains only public API symbols and includes all public `LyraClient` / `AsyncLyraClient` resource methods; `docs/dist/api/client-exports/` no longer exists.
- [ ] Zero false-positive inline-code autolinks on bare lowercase tokens (`execute()`, `context`, `parquet`, `location_id`, `per_location`, `executed`).
- [ ] All `docs/public/examples/mcp/*.json` files pass strict Pydantic validation.
- [ ] Zero `<span id=` tags, zero `contentPlan:`/`contentType:` frontmatter fields, zero `docs/archive/`, zero `docs/content-plan.json`, zero `docker/docker-compose.yml`, and zero `lyra.utils.geometry` / `lyra.contracts.geometry` references remain.
- [ ] `cd docs && npm run ci` and all affected pytest suites pass cleanly.
