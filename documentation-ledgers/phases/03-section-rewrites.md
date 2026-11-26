# Phase 3: Section-by-Section Rewrites, Fact Fixes, & Style Polish

**Goal:** Rewrite each consolidated documentation section in one cohesive pass—fixing every factual discrepancy (`FACT-*`), onboarding gap (`UX-*`), and voice/style defect (`STYLE-*`) together so each page is touched once.

**Prerequisites:** Phase 1 (tooling & contracts) and Phase 2 (locked 49-page IA & `docs/navigation.json`) are complete and green.

**Already resolved before this overhaul (no action needed):** `FACT-17` (resolved in `0cf17003`), `FACT-22` (resolved in `20c90a3b`), and `FACT-38` (resolved in `0cf17003` & `16ed7c65`).

---

## House Style & Voice Rules (Apply in Every Batch)

Refer to [`../05-style-jargon-and-formatting.md`](../05-style-jargon-and-formatting.md) plus our settled defaults during every rewrite:

- **Vocabulary:**
  - Replace `retained` with **saved** (completed results/reports), **active / live job** (consumer docs), or **RQ queue record** (`operate/` & `concepts/lifecycle.md` only). Keep Valkey and queue-expiry caveats out of Start Here and `use/*`.
  - Replace overloaded `native` with the concrete thing (**DataFrame / GeoDataFrame**, **target provider records**, **original CRS**, **Python `Intervention`**).
  - Replace `publication`/`publish` (for local stores) with **save / commit**; replace `live definitions` with **Python definitions / registered plugin**; drop filler `ordinary` (13×); replace `agent Bearer credential` with **agent key**; replace `REST guide` with **HTTP API**.
  - Use **metric function** for `@metric` handlers and **callback** for operation mappers/selectors. Reserve **target selector** for Ibis `target_selector` (use **location operation** for `@location_operation`).
  - Define `interventions`, `correspondence`, and `effective` parameters/datasets on first use with a 1-line example.
  - Standardize on primary method spellings `client.results.tables()` and `store.describe()` (note aliases once in reference).
- **Eliminate spec-sheet / AI-prose tells (`STYLE-01`–`STYLE-06`):** Cut defensive negative-invariant chains, `"X does not / cannot prove Y"` disclaimers, agent-skill rubric prose, identical `"Start with ...; you’ll..."` openers, passive voice, and weasel words (`Typical use`, `roughly every five seconds`, `within approximately two minutes`).
- **Formatting (`STYLE-07`–`STYLE-14`):** US English; sentence-case sidebar labels matching H1s; unwrapped single-line paragraphs with curly apostrophes (`’`); `64 KiB` everywhere; plain HTTP status codes (`HTTP 404`); closed prefixes (`nonempty`, `nonblank`, `nonnegative`, `noncanonical`) with hyphenated `non-null`; consistent contractions; rejoin split `@metric`/`@location_operation` decorators with their `def`; verify every code line-highlight range.
- **Parallelization note:** Once Phase 2 is locked, Batches **3A**, **3B**, **3D**, and **3E** edit largely disjoint doc files and can be drafted in parallel if desired (coordinate shared edits to `tests/test_docs_contract.py` and `README.md`). Isolate **Batch 3C** (City Plugin Tutorial) in its own focused commit because it couples Markdown, `examples/city-plugin/`, and `tests/tutorial_helpers.py`.

---

## Batch 3A — Start Here & Understand Lyra (`index.md`, `start/*`, `quickstart.mdx`, `concepts/*`)

**Target files:** `index.md`, `start/installation.md`, `start/local.mdx`, `quickstart.mdx`, `concepts/how-lyra-works.md`, `concepts/graphs.md`, `concepts/lifecycle.md`, `concepts/caching-and-revisions.md`, `concepts/provenance.md`.

### Start Here (`UX-01`–`UX-03`, `FACT-01`, `FACT-36`)
- [ ] **`index.md` (`UX-03`, `FACT-01`, `FACT-36`):**
  - Lead with metrics, plugins, and local execution; absorb the concise local-vs-server table from deleted `start/choose-execution.md`.
  - Fix `FACT-01`: state *"Select locations by provider ID and record IDs"* (not dataset/feature type/IDs).
  - Fix `FACT-36`: state that completed jobs and results stay saved durably in SQLite (not *"for a configured period"*).
  - Keep Valkey out of Start Here (at most one brief link to `operate/` in an *"Operating a server"* note).
- [ ] **`start/installation.md` (`IA-03`):** Tighten extra explanations (`lyra[sdk]`, `lyra[runtime]`, `lyra[client,tables]` vs `lyra[client,data]`, `lyra[server]`).
- [ ] **`start/local.mdx` (`UX-01`, `UX-02`, `STYLE-07`, `STYLE-10`):**
  - Rename the script from `first_metric.py` to `try_local.py` (avoids Ch. 1 collision) and update `test_published_local_tutorial_runs_without_services` in `tests/test_docs_contract.py:560-574`.
  - Trim premature `.tables`/`.metadata`/`.report` internals (`L50`); focus on running a metric and reading its DataFrame.
  - Fix UK `metres` → `meters` and remove `&nbsp;` (`100 m`).
- [ ] **`quickstart.mdx` (`IA-05`):** Polish steps, verify `curl` payloads stay valid for `test_server_quickstart_request_runs_with_deployment_plugins` (`tests/test_docs_contract.py:658`).

### Understand Lyra (`UX-19`–`UX-23`, `FACT-02`, `FACT-03`, `FACT-36`)
- [ ] **`concepts/how-lyra-works.md` (`UX-19`, `UX-20`, `UX-22`, `FACT-03`, `FACT-36`):**
  - Center on metrics, providers, dependency graphs, and local vs. server execution; briefly introduce the city running example (`city_street_maintenance`, `city_block_maintenance`, `city_streets_for_blocks`) without assuming readers finished the tutorial (`UX-20`).
  - Keep Valkey scoped strictly to the brief *Server architecture* subsection.
  - Fix `FACT-03`: *"Public routes expose health, metric schemas, and the location-selection schema."*
  - Fix `FACT-36`: completed jobs and results are stored durably in SQLite; live RQ queue records and admin tracebacks expire after `jobs.rq_retention_seconds`.
  - State the mutation-isolation guarantee cleanly once here (`IA` duplication inventory).
  - Add **Mermaid diagram #1** (`UX-22`): local in-process runner vs. server architecture (API, Valkey/RQ queue, workers, shared SQLite store).
- [ ] **`concepts/graphs.md` (`FACT-02`, `UX-22`, `STYLE-09`):**
  - Fix `FACT-02`: remove retired `"Shared family structures and "`.
  - Integrate location routing cleanly from merged `selection.md`; delete copy-pasted transaction-atomicity paragraph (`L70-76`).
  - Add **Mermaid diagram #2** (`UX-22`): dependency graph + canonical location routing.
- [ ] **`concepts/lifecycle.md` (`IA-44`, `STYLE-07`):**
  - Cover accepted computation-plan verification (`bound_plan`), active queueing (Valkey/RQ + `jobs.rq_retention_seconds`) vs. durable terminal history in SQLite, timeouts, and cancellation. Fix `cancelling` → `canceling`.
- [ ] **`concepts/caching-and-revisions.md` (`IA-45`, `UX-22`, `UX-23`, `STYLE-01`, `STYLE-09`):**
  - Single canonical home for the **2-row revision table** (*"What revision do I bump?"*: `code_revision` vs. `origins`/provider identity) and the single non-duplicated transaction-atomicity note.
  - Show a concrete **what breaks** example when `code_revision` or `origins` isn’t bumped (`UX-23`), plus overlapping selections; trim storage-engine internals.
  - Add **Mermaid diagram #3** (`UX-22`): `full` vs. `per_location` cache partitioning.
- [ ] **`concepts/provenance.md` (`IA-46`, `UX-21`, `STYLE-01`):**
  - Condense (~164L → ~90L); open with a small, concrete `ExecutionReport` example before field tables; explain why `ExecutionReport` exists alongside transient `runner.inspect()`; strip defensive negative-invariant chains.

### Batch 3A Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py -k "local_tutorial or server_quickstart or navigation"
```

---

## Batch 3B — Run Metrics (`use/*`)

**Target files:** `use/local-execution.md`, `use/python-client.md`, `use/rest-api.md`, `use/mcp.md`, `use/spatial-inputs.md`, `use/jobs.md`, `use/results.md`.

- [ ] **`use/results.md` (`IA-12`, `IA-13`, `UX-04`, `UX-05`, `UX-06`, `FACT-29`, `FACT-39`, `STYLE-01`, `STYLE-05`, `STYLE-10`):**
  - Single source of truth for result retrieval (~406L → ~240L). **Invert the page** so the happy path comes first:
    1. Crisp **output-format / method decision matrix** (DataFrames via `client.results.tables()`/`table()`, full JSON via `get()`/`download()`, preview via `inspect()`, metadata via `describe()`, provenance via `reports.get()`, local file via `ResultStore`).
    2. Reading DataFrames (`client.results.tables()` / `table()`).
    3. Descriptions & execution reports.
    4. Location inputs & **GeoParquet exports** (concise ~35L section absorbed from deleted `use/location-exports.md` with one concrete spatial example noting `export_geoparquet` requires a spatial table and raises `ValueError` on nonspatial `maintenance`, dropping the 3 repetitive wrappers).
    5. Reopening local SQLite results (`reopen_result.py` — keep compatible with `test_retained_result_example_reopens_detached_values`).
    6. Row identity, CRS, mutation isolation, and durability (single short callout for atomic writes/temp cleanup).
  - Remove all Valkey / `"Keep access after queue expiry"` framing (`UX-04`, `FACT-39`); talk purely in terms of *active jobs* vs. *completed jobs*.
  - Fix `FACT-29` & `FACT-39`: list all 5 `ResultAccessError` subclasses (including `LocationInputUnavailableError` imported from `lyra.contracts.errors`, not `lyra.api`); include `kind` and `status` in the full JSON result table; note `GET /jobs/{job_id}` can also return `HTTP 500` `invalid_job_result` on damaged SQLite terminal metadata or missing stored outputs.
- [ ] **`use/python-client.md` (`IA-07`, `UX-06`, `FACT-04`, `FACT-08`, `FACT-37`, `STYLE-05`, `STYLE-06`):**
  - Trim ~367L → ~200L: happy path first (connect → discover → submit/wait → `client.results.tables()`), cut ~110L of duplicated result-store/location-input/export prose and stray stubs (`L354,358`), link to `use/results.md` and `use/jobs.md`.
  - Fix `FACT-04`: `HTTP 404` raises `DownloadError`; structured `HTTP 500` `invalid_job_result` raises `ServiceUnavailableError` (`retryable=False`); plain-string `HTTP 503` raises `DownloadError`.
  - Fix `FACT-08`: specify `client.results.tables()` / `client.result_tables()` raise `MetricRunError` on failed jobs, whereas `client.results.table()` / `download_table()` raise `ResultNotFoundError` (`HTTP 404`).
  - Fix `FACT-37`: change `client.results.inspect(job_id)` description from `"metadata and lifetime"` to `"output summaries and previews"`.
  - Fix `UX-06`: remove unused `JsonObject` import, avoid appending a second `with LyraClient(...)` block to the same script, clarify `lyra[client,tables]` vs `lyra[client,data]` once, and note `LocationInputUnavailableError` comes from `lyra.contracts.errors`.
- [ ] **`use/rest-api.md` (`IA-08`, `UX-07`, `FACT-05`, `FACT-29`, `STYLE-10`, `STYLE-13`):**
  - Rewrite ~202L → ~140L with **runnable `curl` commands** (keep JSON request blocks valid for `test_published_requests_validate_against_example_manifest`), add `GET /executions/{job_id}/report/inspection`, and cut duplicated Arrow/error tables (link to `use/results.md` + `reference/generated/http.md`).
  - Fix `FACT-05`: remove the myth that cross-field Python validators run only in workers.
  - Fix `FACT-29`: replace `"result files"` with SQLite result store; fix highlight range (`{3-10}` → `{3-9}`).
- [ ] **`use/mcp.md` (`IA-09`, `UX-07`, `FACT-06`, `FACT-29`, `FACT-39`, `STYLE-10`, `STYLE-13`):**
  - Refocus ~237L → ~140L around wiring an AI assistant (real MCP client config snippet with endpoint URL + `Authorization: Bearer` header) and a 4-step prompt-to-tool workflow; cut unnumbered protocol-spec sections (`L104-237`) that duplicate `reference/generated/mcp.md` and `use/results.md`.
  - Fix `FACT-06`: downloaded result identifies the metric at `provenance.accepted.plan.request.metric` (or `metric` on `lyra_get_job_result`), not top-level `metric`.
  - Fix `FACT-29` & `FACT-39`: inspection numeric bounds require declared dtype in `{"int64", "Int64", "float64", "Float64"}` and `count > 0`; lookup errors include `input_name=null`; drop post-queue-expiry table framing (and include `lyra_download_location_input` if referencing download tools); fix highlight range (`{3-10}` → `{3-9}`).
  - Keep inline/linked JSON payloads compliant with `test_mcp_workflow_examples_match_serialization_schemas`.
- [ ] **`use/local-execution.md` (`IA-06`, `UX-06`, `FACT-29`, `FACT-34`, `STYLE-05`, `STYLE-13`):**
  - Remove stray tail paragraph (`L240`); make snippets self-contained (`L110-120`, `L170`, keeping `run_installed.py` passing `test_installed_plugin_guide_runs_without_services`).
  - Give exact import paths for all 8 listed exceptions (`L224-230`) and warn that `LocalRunner(..., result_store_path=...)` requires `pathlib.Path` (`str` raises `AttributeError`), unlike `ResultStore.open()`.
  - Fix `FACT-29` (distinguish provider resolution preserving first-occurrence order vs. unordered stored result tables), `FACT-34` (link directly to `plugins/resources/#define-an-io-resource`), and highlight range (`{5-7}` → `{6-8}`).
- [ ] **`use/spatial-inputs.md` (`IA-10`, `FACT-07`):**
  - Define `interventions` clearly with a 1-line example; remove hardcoded GitHub `main` link.
  - Fix `FACT-07`: distinguish empty primary inputs on location-scoped metrics (handler skipped, typed empty outputs returned) from unpartitioned or non-primary empty inputs (handler runs with empty frame).
- [ ] **`use/jobs.md` (`IA-11`, `FACT-04`, `FACT-05`, `FACT-29`, `STYLE-06`):**
  - Slim, single home for polling, retries, and failure recovery.
  - Fix `FACT-04` (`ServiceUnavailableError` for structured service-error responses like `HTTP 500` `invalid_job_result`), `FACT-05` (distinguish `HTTP 404`/`422` submission errors from worker errors including `bound_plan`), `FACT-29` (5 polling retries back off ~1, 2, 4, 8, 16 s—cannot reach 30 s), and `STYLE-06` (remove `"roughly every five seconds"` and passive voice).

### Batch 3B Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py -k "mcp_workflow or published_requests or installed_plugin_guide or retained_result"
```

---

## Batch 3C — City Plugin Tutorial (`plugins/tutorial/*`, `examples/city-plugin/`, `tests/tutorial_helpers.py`)

**Target files:** `plugins/tutorial/{index.md, first-metric.mdx, select-streets.mdx, parameters.mdx, dependencies.mdx, canonical.mdx, blocks.mdx, test-and-package.mdx}`, `examples/city-plugin/` (`# docs:start` regions), `docs/src/components/{BlockWorkloadDiagram,MaintenanceDependenciesDiagram}.astro` (delete), `docs/scripts/generate_docs.py` (drop diagram strip regexes), `tests/tutorial_helpers.py`, `tests/test_docs_contract.py`.

- [ ] **Delete both broken Astro SVG components & replace with Mermaid (`FACT-10`, `FACT-30`, `UX-10`):**
  - Delete `docs/src/components/BlockWorkloadDiagram.astro` and `docs/src/components/MaintenanceDependenciesDiagram.astro`.
  - Remove their strip regexes from `docs/scripts/generate_docs.py` (`normalize_mdx()`) and corresponding assertions from `tests/test_docs_contract.py:130-131,146-147`.
  - Author clean, accurate fenced `mermaid` blocks in `dependencies.mdx` (include `Selected streets` input with clean edges) and `blocks.mdx` (accurate topology: `Selected blocks` fans out directly to (a) `streets_for_blocks` → `CityStreetProvider` → `street_maintenance`, (b) `street_membership`, and (c) `block_maintenance` directly).
- [ ] **Eliminate throwaway-file churn & standardize module structure (`UX-08`, `STYLE-04`, `STYLE-11`, `STYLE-12`, `STYLE-14`):**
  - Build progressively in `city_example/tutorial.py` from Chapter 1 using `create_plugin()` consistently across every chapter (no throwaway `first_metric.py` / `selected_metric.py` / `maintenance_metric.py` files).
  - Guard runnable scripts with `if __name__ == "__main__":` so pytest imports never execute runs or mutate `.lyra/results.sqlite3`.
  - Rewrite identical `"Start with ...; you’ll..."` chapter openers (`STYLE-04`); standardize all 7 chapters on `.mdx` + `<Steps>` + `<CodeExample>` (`STYLE-11`); never split `@metric`/`@location_operation` from `def` (`STYLE-12`); use keyword `LocationDependency(operation="...")` (`STYLE-14`); wrap lines >80 cols in `# docs:start` regions and `dependencies.mdx`.
- [ ] **Chapter-by-chapter fixes (`FACT-09`–`FACT-12`, `FACT-30`, `UX-09`, `STYLE-07`, `STYLE-13`):**
  - **`index.md` (`IA-14`):** Update chapter map & titles for 7 chapters (aligned with page titles & sidebar labels); trim repeated `uv sync`.
  - **Ch. 1 (`first-metric.mdx`, `UX-09`, `FACT-30`, `STYLE-07`, `STYLE-13`):** Don’t highlight `code_revision`/`origins` without explaining them; note `column(...)` is a helper in `city_example/schemas.py` and explain the `{"city": ...}` namespace key; fix `LocalRunner.run()` returns `RunResult` (not dict); fix UK `centreline`/`metre`/`metres`; fix `calculations.py#lengths` highlight range.
  - **Ch. 2 (`select-streets.mdx`, `UX-09`, `FACT-30`):** Embed `# docs:start provider` (syncing unused region); explain why `LocationProvider` registers under `resources=`; clarify `STREETS` (`"city_streets"`) is the provider ID string; explain `self.operation("resolve")` if highlighted.
  - **Ch. 3 (`parameters.mdx`, `FACT-11`, `FACT-30`, `STYLE-07`, `STYLE-13`):** Fix invalid Pint unit `"score"` → `"dimensionless"`; clarify `metadata={"granularity": ...}` is informational; fix UK spellings; fix `metrics.py#parameters` and `checkpoints.py#direct` highlight ranges.
  - **Ch. 4 (`dependencies.mdx`, `FACT-09`, `FACT-30`, `STYLE-13`):** Fix broken `uv run pytest -n auto tests/test_tutorial.py` → `uv run pytest tests/test_tutorial.py`; embed `# docs:start maintenance`; add accurate Mermaid dependency diagram; fix highlight ranges.
  - **Ch. 5 (`canonical.mdx`, `IA-19`, `UX-09`, `FACT-12`, `FACT-30`, `STYLE-12`):** Introduce `CityBlockProvider`’s `street_records`/`allocations` columns; write `@location_operation(..., canonical=True)` mapping blocks → street IDs (`dict[str, tuple[str, ...]]`, synced with `# docs:start selector`, no dead `LineString` import or leftover pre-provider prose); immediately run `city_street_maintenance` with block IDs; assert `dtypes` in `test_empty_block_returns_typed_empty_streets()`.
  - **Ch. 6 (`blocks.mdx`, `IA-19`, `UX-09`, `FACT-10`, `FACT-12`, `STYLE-12`, `STYLE-13`):** Add `street_membership` + weighted `city_block_maintenance`; include corrected Mermaid workload diagram; explain why unused `parameters: MaintenanceParameters` is declared on `block_maintenance` (public schema exposure); fix all highlight ranges.
  - **Ch. 7 (`test-and-package.mdx`, `IA-20`, `UX-08`, `UX-09`, `FACT-30`):** Combine trimmed testing (`result.report` + `resource_overrides` failure test, dropping the confusing `runner.inspect()` deep dive, showing concrete sample output, explaining `.lyra/results.sqlite3`, dropping unexplained *"service-access guard"* claim) with packaging (`create_plugin` export, warning before editing `city_example/__init__.py`, `lyra-plugin describe`/`diagram`). Keep `uv run python -m city_example <N>` checkpoint numbers aligned with `examples/city-plugin/city_example/__main__.py`.
- [ ] **Update tutorial test harness (`tests/tutorial_helpers.py` & `tests/test_docs_contract.py:787-900`):**
  - Update `city_tutorial_project()` and `verify_city_tutorial_workspace()` for the 7-chapter single-module `city_example/tutorial.py` layout and `if __name__ == "__main__":` runners (invoking the chapter `main()` / runner function cleanly without import-time side effects), preserving full progressive verification across all 7 chapters plus `verify_city_tutorial_manifest()`.

### Batch 3C Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py -k "city_tutorial or mdx_exports"
```

---

## Batch 3D — Standalone Plugin Guides & Authoring Skill (`plugins/*`, `skills/lyra-metric/`)

**Target files:** `plugins/{index.md, authoring.md, outputs.md, dependencies.md, location-operations.md, location-providers.md, resources.md, earth-engine.md, testing.md, development.mdx, packaging.md, agent-assisted-authoring.mdx}`, `skills/lyra-metric/references/authoring.md`.

- [ ] **`plugins/index.md` (`IA-21`, `FACT-33`):** Accurate guide matrix covering the tutorial + all 11 standalone guides; link to `plugins/packaging.md` for starting from scratch.
- [ ] **`plugins/authoring.md` (`IA-22`, `IA-24`, `UX-12`, `UX-15`, `FACT-28`, `FACT-32`, `STYLE-01`–`STYLE-03`, `STYLE-13`):**
  - Complete How-to for writing and registering metrics: include one complete `@metric` combining `MetricParameters`, `LocationDependency`, and `outputs`, plus factory pattern, `private=True`, `code_revision`, `origins`, and a concise *"Write clear descriptions"* section (folded from `descriptions.md`, with before/after Python example, noting only MCP listings abbreviate descriptions while HTTP `GET /metrics` returns full descriptions — `FACT-32` — and stripping agent-skill rubric prose — `STYLE-03`).
  - Document split-plugin caveat (`FACT-28`: `PluginDefinition.describe()` / `.implementation()` raises `ValueError: Unknown provider` when referencing a provider registered in another `PluginDefinition`).
  - Cut parameter-spec & cache-key dumps (link to `reference/parameters.md` & `concepts/caching-and-revisions.md`); fix highlight range (`{6-10}` → include closing `)` on L11); keep JSON request blocks passing `test_published_requests_validate_against_example_manifest`.
- [ ] **`plugins/outputs.md` (`IA-23`, `UX-12`, `FACT-26`, `STYLE-01`, `STYLE-02`, `STYLE-13`):**
  - Add concrete `primary=True` location-scoped (`location_id`, `row_key`, composite uniqueness) and multi-output (`dict[str, pd.DataFrame]`) metric examples; include output `meaning`/`granularity` moved from `metrics.md`.
  - Fix `FACT-26`: document rejected Pandera constructs (`coerce=True`, column `default=`, unnamed checks, schema-level `dtype`) and reserved columns (`location_id`, `row_key`, `source_location_id`).
  - Cut SQLite internals & Hypothesis dupes; fix highlight range (`{5,6}` → `{4,5}`).
- [ ] **`plugins/dependencies.md` (`IA-25`, `UX-12`, `UX-13`, `FACT-13`, `FACT-15`, `STYLE-13`):**
  - Refocus as concise How-to on `Dependency`, `.bind()`, parameter forwarding, and `lyra-plugin diagram`; cut the duplicated city-tutorial rebuild (`L15-83`); print `tables["maintenance"]` in `maintenance_graph.py` (keep compatible with `test_published_dependency_example_runs_with_a_fake_source`).
  - Fix `FACT-13` (remove `calculate_feature_areas_m2`, recommend GeoPandas `.to_crs(...).geometry.area`) and `FACT-15` (remove vestigial claims that location operations own dependencies/bindings); fix highlight range (`L116`).
- [ ] **`skills/lyra-metric/references/authoring.md:348` (`FACT-13`):** Remove the unusable `lyra.utils.geometry.calculate_feature_areas_m2` recommendation; recommend GeoPandas `.to_crs(...).geometry.area`.
- [ ] **`plugins/location-operations.md` (`IA-26`, `UX-12`, `FACT-15`, `FACT-27`, `FACT-31`, `STYLE-02`, `STYLE-07`, `STYLE-12`, `STYLE-13`):**
  - Concise How-to absorbing `canonical-selection.md`: concrete `@location_operation`, `canonical=True`, `route_preferences=[...]`, caching, and two-argument `(source, candidates)` signature when using `target_selector` (`FACT-27`).
  - Clarify only `canonical=True` operations require defaults on every parameter field (`FACT-27`); remove operation dependency/fixed-binding claims (`FACT-15`); fix stale `tests/test_location_examples.py` description (`FACT-31`); fix UK `ten-metre` → `ten-meter`; rejoin split decorators (`STYLE-12`); fix highlight range (`L116`); keep `derived_example.py` passing `test_published_derived_location_graph`.
- [ ] **`plugins/location-providers.md` (`IA-27`, `IA-28`, `UX-12`, `UX-13`, `UX-15`, `FACT-14`, `FACT-26`, `STYLE-01`, `STYLE-02`, `STYLE-10`):**
  - Rewrite as a practical How-to with concrete Python examples: custom `LocationProvider` (including mandatory Pandera checks on `location_id` and `nullable=False` on `geometry`, `FACT-26`), `ParquetLocationProvider`, `PostGISLocationProvider`, and a concise two-plugin provider/metric example (keeping behavioral lifecycle & missing-ID coverage in `test_published_location_graph`).
  - Fix `FACT-14`: document returning only matching rows so Lyra raises `LocationProviderContractError` with exact missing IDs, and note exceptions live in `lyra.contracts.errors`.
  - Align synthetic example conventions (`UX-13`); fix GitHub `/lyra/` → `/Lyra/` casing.
- [ ] **`plugins/resources.md` (`IA-29`, `UX-12`, `UX-15`, `STYLE-13`):**
  - Covers `BaseIO`, `PostgresIO`, and `RunContext` (`logger`, `temp_dir`, `report_progress` from merged `runtime.md`).
  - Show running `observations` via `LocalRunner.from_definitions(..., resource_config=...)`, align `directory` with `example-data`, move Parquet fixture creation out of module import scope (keeping `test_resource_guide_execution_and_inherited_dependency` green), and fix out-of-bounds highlight range (`{6,9-11}` on 9-line block at `L158`).
- [ ] **`plugins/earth-engine.md` (`IA-30`, `UX-12`, `STYLE-07`, `STYLE-10`):**
  - Keep standalone (per settled decision); add H2 headings, add a complete `@metric` consuming `EarthEngineIO` via `Annotated[EarthEngineIO, Resource(...)]`, and fix UK `metres` → `meters`.
- [ ] **`plugins/testing.md` (`IA-31`, `UX-14`, `UX-15`, `STYLE-02`, `STYLE-13`):**
  - Document `LocalRunner(..., result_store_path=tmp_path / "results.sqlite3")`; drop unexplained repo-internal *"service-access guard"* / `no_external_services` assumption; fix dead `.tables` (`L106`) and meaningless highlight range (`{2-6}`); keep `test_plugin.py` and `test_graph.py` passing `test_docs_contract.py`.
- [ ] **`plugins/development.mdx` (`IA-33`, `UX-15b`, `FACT-40`, `FACT-41`):**
  - Polish native `lyra-plugin dev` guide: document `set -a; source ...; set +a` for `keys.env`, XDG cache directory (`$XDG_CACHE_HOME/lyra/lyra-dev-*` or `~/.cache/lyra/lyra-dev-*`), ephemeral private `valkey-server` on `/tmp/lyra-*/sock` (no TCP port conflicts; SQLite results & `keys.env` persist while live queue does not), accepted `--valkey-url` schemes (`redis://`, `rediss://`, three-slash `unix:///`; why `valkey://` is rejected; 10 s readiness probe), note MCP is disabled (`mcp.enabled = False`), and link cleanly to `operate/configuration/` (instead of deleted `operate/resources/`).
- [ ] **`plugins/packaging.md` (`IA-32`, `UX-11`):**
  - Add a **Start a new plugin from scratch** template: minimal complete `pyproject.toml` (`[build-system]`, `dependencies = ["lyra[sdk]"]`, `[dependency-groups] dev = ["lyra[runtime]", "pytest"]`, `[project.entry-points."lyra.plugins"]`) + package directory layout, entry-point wiring, `lyra-plugin describe`/`diagram`, and operator handoff.
- [ ] **`plugins/agent-assisted-authoring.mdx` (`IA-34`, `FACT-16`):**
  - Fix `FACT-16`: replace leftover *"named feature collections with a CRS"* with `GeoDataFrame` phrasing.

### Batch 3D Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py -k "plugin_test or graph_examples or dependency_example or location_graph or derived_location or resource_guide or published_requests"
```

---

## Batch 3E — Deploy & Operate (`operate/*`, `README.md`) & Handwritten Reference (`reference/*`)

**Target files:** `operate/{deployment.mdx, configuration.md, plugins.mdx, monitoring.md, updates.mdx, troubleshooting.md}`, `reference/{index.md, python.md, parameters.md, target-selection.md, units.md, location-artifacts.md, mcp-payloads.md}`, root `README.md`.

### Deploy and Operate (`UX-16`–`UX-18`, `FACT-05`, `FACT-18`–`FACT-21`, `FACT-23`–`FACT-25`, `FACT-35`, `FACT-39`–`FACT-41`, `STYLE-05`–`STYLE-14`)
- [ ] **`operate/deployment.mdx` (`IA-35`, `IA-36`, `UX-16`, `UX-17`, `UX-18`, `FACT-25`, `FACT-35`, `STYLE-14`):**
  - Open with *"Before you start"* (from merged `requirements.md`), listing `uv` if validating offline on the host (or validating inside the container).
  - Reconcile Docker files cleanly (`UX-16`): position `examples/lyra-deployment/` as the canonical Docker Compose deployment template and note in one sentence that root `Dockerfile` builds the bare core image without plugins.
  - Fix step sequence (`UX-17`): briefly explain minimal `lyra.toml` before asking to edit `api.public_base_url`; remind operators to `export LYRA_ADMIN_API_KEY=...` when switching from container `docker compose exec api lyra-admin` to host `lyra-admin`.
  - Include `/lyra_data/secrets/` in the `/lyra_data/` layout (`FACT-25`); add **bare-metal / non-Docker launch commands** (`python -m lyra.server.main --config ...` and `python -m lyra.server.worker_launcher --config ...`, defaulting to `/lyra_data/config/lyra.toml`, `UX-18`).
  - Standardize `lyra-admin` command prefixes (`STYLE-14`).
- [ ] **`operate/configuration.md` (`IA-37`, `UX-18`, `FACT-18`, `FACT-19`, `FACT-23`, `FACT-25`, `FACT-39`, `FACT-40`, `FACT-41`):**
  - Include a **minimal complete `lyra.toml`** (`schema_version = 8`, with `[valkey]`).
  - Document `--config` alongside `/lyra_data/config/lyra.toml` (`FACT-40`); note `mcp.mount_path` rejects trailing `/`, `api.public_base_url` rejects single-label Docker hostnames (`FACT-25`), and `config-summary` reports only the API process’s subset (`FACT-25`).
  - **No reverse-proxy example** (settled decision): keep only a brief note on configuring `api.public_base_url`, `api.forwarded_allow_ips`, and preserving the `Host` header for MCP DNS-rebinding protection when deploying behind your own proxy.
  - **Resources section** (from merged `resources.md`): add a realistic **PostGIS** `[resources.*]` TOML + secret-file example (`UX-18`); delete obsolete single-provider claim (`FACT-18`, update `L30` too).
  - **SQLite & storage section** (from merged `caching.md`): use literal `/lyra_data/results.sqlite3` (not `LYRA_DATA_DIR`, `FACT-23`); add explicit **single-host / no-NFS** warning, safe **online SQLite backup**, and **scratch-dir cleanup** (`/lyra_data/cache/jobs` / `worker.temp_dir`); emphasize SQLite holds durable terminal job history + cached results + reports and drives `/ready` `HTTP 503`; remove false paginated HTTP/MCP row claim (`FACT-19`) and clarify cache policies are declared in plugin code (`FACT-25`); use `"Queue expiry"` (`FACT-39`).
  - **Valkey section (`FACT-41`, `UX-18`):** Document exact accepted `valkey.url` schemes (`redis://`, `rediss://`, three-slash absolute `unix:///`, explaining why `valkey://` is rejected), warn against `allkeys-lru` evicting active RQ keys, and explain `/ready` `ready` vs. `degraded` (completed jobs stay readable via SQLite; new submissions & live queue queries fail) and auth/TLS (`rediss://`).
  - Move Python embedding (`create_app`/`launch_worker`) out of operator config (point to `reference/python.md` if needed); fix link wording (`FACT-40`).
- [ ] **`operate/plugins.mdx` (`IA-38`, `FACT-40`, `STYLE-10`):** Tighten production image plugin installation & multi-provider setup; link directly to `plugins/development/` for local dev (redirect stub already removed in Phase 2).
- [ ] **`operate/monitoring.md` (`IA-39`, `FACT-20`, `FACT-21`, `FACT-25`, `FACT-40`, `STYLE-05`, `STYLE-06`, `STYLE-14`):**
  - Combine health checks (`/ready` `ready`/`degraded`/`not_ready`), structured JSON logs on **`stdout`** (matching Batch 1A’s `logging_config.py` fix, noting Uvicorn/RQ log separation, `FACT-20`), `lyra-admin` & `LyraAdminClient` (explicitly noting CLI defaults to HTTP `secure=False` while Python admin clients default to HTTPS `secure=True`, and `lyra-admin jobs list` requires `--status`, `FACT-25`), RQ interruption recovery, `jobs.rq_retention_seconds`, and SQLite terminal history during Valkey outages.
  - Remove nonexistent `"observation metadata"` claim (`FACT-21`), weasel words (`STYLE-06`), and broken anchor links (`FACT-40`).
- [ ] **`operate/updates.mdx` (`IA-40`, `FACT-41`):** Cut duplicated revision prose to a 2-sentence summary linking to the 2-row table in `concepts/caching-and-revisions.md`; keep `rq empty` + `docker compose up --wait` procedure aligned with the strengthened `/ready` healthcheck (`FACT-41`).
- [ ] **`operate/troubleshooting.md` (`IA-41`, `FACT-05`, `FACT-21`, `FACT-24`, `STYLE-10`, `STYLE-11`):** Keep as `.md` (symptom → diagnosis → fix tables); fix `FACT-05` (no worker-only cross-field validator myth), `FACT-21` (no `"observation metadata"`), `FACT-24` (include `bound_plan`, fix field name `path`/`receiving_input` instead of `"input"`, no `deployment_changed`); standardize table-cell punctuation.
- [ ] **Root `README.md`:** Verify all links, terminology (`HTTP API`, no stale `requirements/` or `admin-cli/` URLs), and `uv run python -m city_example <N>` checkpoint count match the updated docs.

### Handwritten Reference (`IA-47`–`IA-53`, `FACT-27`, `FACT-39`, `STYLE-01`, `STYLE-07`, `STYLE-10`)
- [ ] **`reference/index.md` (`IA-47`, `STYLE-10`):** Match consolidated page list and fix GitHub `/lyra/` → `/Lyra/` casing.
- [ ] **`reference/python.md` (`IA-48`, `TOOL-03`):** Expand overview to cover `location_operation`, `RunContext`, public client resource classes, `LyraAdminClient`, `AsyncLyraAdminClient`, `JobHandle`, `RunOptions`, and `lyra.utils` (`date`, `ee` — no retired `geometry`); verify all links resolve against scoped `starlight-pydocs`.
- [ ] **`reference/parameters.md` (`IA-49`, `FACT-27`):** Add `Resource(...)` arguments to the handler signature table and include location operation names in the naming rule; keep `effective_parameters.py` passing `tests/test_documented_parameters.py`.
- [ ] **`reference/target-selection.md` (`IA-50`, `STYLE-07`):** Keep strong `L18-144`, trim internal staging/invariant notes (`L154-193`), fix UK `metres`/`metre` → `meters`/`meter`.
- [ ] **`reference/units.md` (`IA-51`, `STYLE-07`):** Fix UK `kilometres`/`metres`/`kilometre` → `kilometers`/`meters`/`kilometer`.
- [ ] **`reference/location-artifacts.md` (`IA-52`, `STYLE-01`):** Rename *"Native tables and GeoParquet exports"* (incorporating merged `reference/locations.md` cleanly) and cut defensive negative-invariant tail (`L80-86`).
- [ ] **`reference/mcp-payloads.md` (`IA-53`, `FACT-39`, `STYLE-10`):** Condense ~385L → ~150L of canonical end-to-end payloads validated against Pydantic models (`TOOL-08`), rename `## Unknown or expired job` → `## Unknown job` (`FACT-39`), and remove Unicode NBSP (`150\u00a0m` → `150 m`).

### Batch 3E Verification Gate
```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py tests/test_docs_generation.py tests/test_documented_parameters.py tests/test_docker_filesystem_layout.py
```
