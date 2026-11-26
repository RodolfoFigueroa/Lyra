# Ledger 02: Factually Wrong, Broken, and Outdated Claims

Every factual discrepancy found across `docs/`, verified directly against `src/lyra/` and `examples/`.

---

## High-Severity Factual Errors & Broken Instructions

### Start Here & Concepts

- `[ ] FACT-01` **Retired `(dataset, feature_type, ids)` location model**
  - **Doc:** `docs/src/content/docs/start/choose-execution.md:35` — *"Select locations by dataset, feature type, and IDs"*
  - **Truth:** `src/lyra/contracts/locations.py:25-35` (`LocationSelection`) accepts only `provider` and `ids`.
  - **Fix:** Change to *"Select locations by provider ID and record IDs."*

- `[ ] FACT-02` **Retired "metric families" concept**
  - **Doc:** `docs/src/content/docs/concepts/graphs.md:33` — *"- Shared family structures and ordinary parameter contracts"*
  - **Truth:** Metric families do not exist in `src/lyra/` (`catalog.py`, `graph.py`).
  - **Fix:** Remove `"Shared family structures and "`.

- `[ ] FACT-03` **Nonexistent "collection metadata" public HTTP route**
  - **Doc:** `docs/src/content/docs/concepts/architecture.md:86` — *"Public routes expose health, metric schemas, and collection metadata."*
  - **Truth:** `src/lyra/server/routes/registration.py:21-32` exposes `/live`, `/ready`, `/metrics`, `/metrics/{metric_name}`, and `/data-types` (`routes/data_types.py:11-16`, returning only `location_selection_schema`). *(Also update stale description `"Select opaque IDs by collection and unit type."` in `src/lyra/contracts/data_types.py:13`.)*
  - **Fix:** Change to *"Public routes expose health, metric schemas, and the location-selection schema."*

### Run Metrics (`use/`)

- `[ ] FACT-04` **Wrong client exception for HTTP 500 `invalid_job_result` (and `503` claim)**
  - **Doc:** `docs/src/content/docs/use/python-client.md:252` claims HTTP `500` `invalid_job_result` raises `DownloadError`; `docs/src/content/docs/use/jobs.md:82` says `ServiceUnavailableError` is for HTTP `503`.
  - **Truth:** `src/lyra/server/routes/job_errors.py:26-38` returns structured `{code, message, retryable}` on HTTP `500`, which `src/lyra/api/client/endpoints.py:139-148` & `base.py:29-56` turn into **`ServiceUnavailableError`** (confirmed by `tests/test_http_job_boundaries.py:229,245-246`). Plain-string HTTP `503` responses (`routes/jobs.py:91`) raise `DownloadError`.
  - **Fix:** State that `404` raises `DownloadError` and structured `500` `invalid_job_result` raises `ServiceUnavailableError` (`retryable=False`). Update `jobs.md:82` to describe structured service-error responses.

- `[ ] FACT-05` **False claim that cross-field Python validators run only in the worker (failing with `invalid_input`)**
  - **Docs:** `docs/src/content/docs/use/jobs.md:58`, `docs/src/content/docs/use/rest-api.md:112`, `docs/src/content/docs/operate/troubleshooting.md:57,62`
  - **Truth:** The API loads live plugins (`server/main.py:44`) and runs full Pydantic validation during `POST /jobs` (`server/job_submission.py:114-119`, `runtime/invocation.py:80`), rejecting cross-field validator errors immediately with HTTP `422` (`routes/jobs.py:88-89`). Worker replay failures raise `BoundPlanError` (`type: "bound_plan"`, `server/worker.py:359-365`), not `invalid_input`.
  - **Fix:** Remove this myth from all 3 pages; distinguish submission errors (`404` unknown metric, `422` invalid parameters/selections/interventions) from worker errors (unknown location IDs, base-dataset intervention conflicts, provider/handler/output failures, `bound_plan`).

- `[ ] FACT-06` **Nonexistent top-level `metric` field on downloaded job result**
  - **Doc:** `docs/src/content/docs/use/mcp.md:100` — *"Use the result’s `metric` field to identify the requested calculation."*
  - **Truth:** `TableJobResult` (`src/lyra/contracts/jobs.py:147-158`) and `DownloadResultOutput` (`server/mcp/models.py:177-188`) have no top-level `metric`. It is at `provenance.accepted.plan.request.metric` (or `metric` on the `lyra_get_job_result` inspection).
  - **Fix:** Correct the field path.

- `[ ] FACT-07` **False claim that empty derived selections always execute downstream handlers**
  - **Doc:** `docs/src/content/docs/use/spatial-inputs.md:73` — *"Selection returns an empty set and downstream handlers still execute"* (contradicts `use/results.md:279`).
  - **Truth:** For location-scoped metrics with an empty primary input, `src/lyra/runtime/invocation.py:224-245` & `execution.py:618-623` skip the handler and return typed empty outputs. Handlers only run when unpartitioned or when a non-primary input is empty.
  - **Fix:** Distinguish empty primary inputs (handler skipped) from unpartitioned/non-primary inputs (handler runs with empty frame).

- `[ ] FACT-08` **False generalization that *"table helpers raise `MetricRunError`"* on failed jobs**
  - **Doc:** `docs/src/content/docs/use/python-client.md:248`
  - **Truth:** `client.result_tables()` / `client.results.tables()` raise `MetricRunError` (`sync.py:356-357`), but `client.results.table()` and `download_table()` hit `/results/{job_id}/tables/download` directly and raise **`ResultNotFoundError`** (`404`).
  - **Fix:** Name `client.result_tables()` and `client.results.tables()` explicitly.

### Plugin Tutorial (`plugins/tutorial/`)

- `[ ] FACT-09` **Broken `pytest -n auto` command in Chapter 4**
  - **Doc:** `docs/src/content/docs/plugins/tutorial/dependencies.mdx:153` — `uv run pytest -n auto tests/test_tutorial.py`
  - **Truth:** `examples/city-plugin/pyproject.toml:16-17` does not install `pytest-xdist`. Command fails immediately with `unrecognized arguments: -n auto`.
  - **Fix:** Change to `uv run pytest tests/test_tutorial.py`.

- `[ ] FACT-10` **Wrong graph topology in Chapter 5 prose AND `BlockWorkloadDiagram.astro`**
  - **Docs:** `docs/src/content/docs/plugins/tutorial/blocks.mdx:15`, `docs/src/components/BlockWorkloadDiagram.astro:4,10-47`
  - **Truth:** Diagram draws `Selected blocks → Membership rows → select street IDs → Load unique streets`. Lyra explicitly forbids `@location_operation` from consuming metric tables (`src/lyra/sdk/plugin.py:289-297`). In `examples/city-plugin/city_example/plugin.py:27-44` & `metrics.py:111-147`, `Selected blocks` fans out directly to (a) `streets_for_blocks` → `CityStreetProvider` → `street_maintenance`, (b) `street_membership`, and (c) `block_maintenance` directly (needed to preserve `block-empty`).
  - **Fix:** Redraw `BlockWorkloadDiagram.astro` and rewrite `blocks.mdx:15-19`.

- `[ ] FACT-11` **Invalid Pint unit `"score"` in Chapter 3**
  - **Doc:** `docs/src/content/docs/plugins/tutorial/parameters.mdx:59` — `| condition_score | Synthetic condition from 0 to 100; score |`
  - **Truth:** Pint rejects `"score"` with `UndefinedUnitError` (`src/lyra/contracts/units.py:25-37`). Code uses `"dimensionless"` (`examples/city-plugin/city_example/schemas.py:44-46,72-74`).
  - **Fix:** Change `; score` to `; dimensionless`.

- `[ ] FACT-12` **Leftover pre-provider prose & dead import in Chapter 5**
  - **Doc:** `docs/src/content/docs/plugins/tutorial/blocks.mdx:30,46,48` — unused `from shapely.geometry import LineString`; heading `"Derive complete street observations"` and `"combines the complete street records stored in each block’s attributes"`.
  - **Truth:** `streets_for_blocks` (`blocks.mdx:63-70`) returns `dict[str, tuple[str, ...]]` (IDs only), contradicting line 72 right below it.
  - **Fix:** Delete unused `LineString` and duplicate imports (`L28,31`); retitle Step 3 (`"Map each block to its street IDs"`) and align line 48 with line 72.

### Standalone Plugin Guides (`plugins/`)

- `[ ] FACT-13` **Unusable legacy `calculate_feature_areas_m2` recommended for plugin handlers**
  - **Doc:** `docs/src/content/docs/plugins/dependencies.mdx:142` (also in `skills/lyra-metric/references/authoring.md:348`)
  - **Truth:** `src/lyra/utils/geometry.py:40-49` expects a Pydantic `GeoJSON` model (`.features`), not a `GeoDataFrame`. Calling it on a handler’s `GeoDataFrame` raises `AttributeError`.
  - **Fix:** Remove the reference; recommend GeoPandas (`.to_crs(...).geometry.area`).

- `[ ] FACT-14` **Wrong `LocationProvider.resolve()` exception contract**
  - **Doc:** `docs/src/content/docs/plugins/location-providers.md:66` — *"Raise `UnresolvedLocationIDsError(self.id, "location", missing_ids)` for unknown IDs..."*
  - **Truth:** `src/lyra/runtime/provider_acquisition.py:213-222` catches *any* exception from `resolve()` (except `ResourceConfigurationError`) and wraps it in `LocationAcquisitionError`, overwriting `missing_ids` with all requested IDs. All real providers in `examples/` and `src/lyra/sdk/managed_provider.py:59` simply return matching rows so `acquire_provider_input()` (`L231-L236`) raises `LocationProviderContractError` with exact missing IDs. Also none of these exceptions are exported from `lyra.sdk` (they live in `lyra.contracts.errors`).
  - **Fix:** Document actual runtime behavior (or decide if `provider_acquisition.py` should let `UnresolvedLocationIDsError` pass through) and show imports from `lyra.contracts.errors`.

- `[ ] FACT-15` **Vestigial claims that location operations can own dependencies or "fixed bindings"**
  - **Docs:** `docs/src/content/docs/plugins/dependencies.mdx:108,182`; `docs/src/content/docs/plugins/location-operations.md:108`; `docs/src/content/docs/plugins/canonical-selection.md:18,32`
  - **Truth:** `src/lyra/sdk/plugin.py:289-297`, `node_declarations.py:212-219`, and `contracts/dependencies.py:128-130` forbid operations from declaring dependencies or `.bind(...)` / `parameters=...` bindings. Canonical operations simply require defaults on all parameter fields.
  - **Fix:** Remove `"or operation"`, `"An operation’s own bindings"`, and `"or fixed bindings"` from all 5 locations.

- `[ ] FACT-16` **Leftover GeoJSON *"feature collections"* claim in agent-assisted authoring**
  - **Doc:** `docs/src/content/docs/plugins/agent-assisted-authoring.mdx:94` — *"Handlers receive named feature collections with a CRS."*
  - **Truth:** Handlers receive `geopandas.GeoDataFrame`s (`skills/lyra-metric/SKILL.md:37`).
  - **Fix:** Replace with `GeoDataFrame` phrasing.

- `[x] FACT-17` **Nonexistent Dockerfile stage `"runtime"`** (`plugins/development.md:48`) — **Resolved in commit `0cf17003`**: the Docker Compose dev workflow and `plugins/development.md` were deleted and replaced by `plugins/development.mdx` (`lyra-plugin dev`).

### Deploy & Operate (`operate/`)

- `[ ] FACT-18` **Obsolete single-location-provider restriction**
  - **Docs:** `docs/src/content/docs/operate/resources.md:38-42` (*"Enable exactly one plugin that declares a location provider... rejects multiple enabled provider declarations"*); echoed in `operate/configuration.md:30` (*"the designated location provider"*).
  - **Truth:** `src/lyra/runtime/planning/provider_registry.py:96-126` supports multiple providers across multiple plugins and only rejects duplicate `provider_id`s. Directly contradicted by `operate/plugins.mdx:73-75` and `examples/city-plugin`.
  - **Fix:** Delete `operate/resources.md:38-42` and update `configuration.md:30`.

- `[ ] FACT-19` **Nonexistent paginated HTTP/MCP location-input row endpoints**
  - **Doc:** `docs/src/content/docs/operate/caching.md:67-68` — *"HTTP/MCP rows use bounded SQL-selected pages."* (Echoed in `docs/content-plan.json:564`.)
  - **Truth:** Neither HTTP (`routes/results.py:42-51`) nor MCP (`mcp/server.py:446-474`) paginates location-input rows; both expose Arrow IPC streaming downloads only.
  - **Fix:** Replace with accurate Arrow streaming behavior.

- `[ ] FACT-20` **Logs default to `stderr`, not `stdout`**
  - **Docs:** `docs/src/content/docs/operate/runbook.md:59` (also `config.example.toml:28`, `examples/lyra-deployment/lyra.toml:29`, `src/lyra/contracts/config.py:249`, `reference/generated/configuration.md:213`).
  - **Truth:** `src/lyra/server/logging_config.py:36-38` uses `logging.StreamHandler()` (defaults to `sys.stderr`) on logger `lyra.server` (`propagate = False`).
  - **Fix:** Either change docs/config descriptions to `stderr` (and note Uvicorn/RQ log separation) or pass `sys.stdout` in `logging_config.py:38`.

- `[ ] FACT-21` **Nonexistent `"observation metadata"` in admin endpoints**
  - **Docs:** `docs/src/content/docs/operate/admin-cli.md:67`, `docs/src/content/docs/operate/troubleshooting.md:16`
  - **Truth:** `src/lyra/contracts/observability.py:21-118` & `admin.py:8-19` emit no observation metadata block.
  - **Fix:** Remove both references.

---

## Medium / Minor Factual Inaccuracies, Omissions, & Drift

- `[x] FACT-22` **Retention vs API access contradiction** (`operate/runbook.md:73`) — **Resolved in commit `20c90a3b`**: `runbook.md:73` now accurately states that SQLite retains terminal status, structured failures, and stored results indefinitely, while `jobs.rq_retention_seconds` governs RQ records and admin tracebacks.
- `[ ] FACT-23` **`LYRA_DATA_DIR` presented as a configurable variable** (`operate/caching.md:15`, `config.example.toml:8`): Hardcoded constant `Path("/lyra_data")` in `src/lyra/contracts/config.py:15`, not an env var. Write `/lyra_data/results.sqlite3`.
- `[ ] FACT-24` **Worker failure types & field names wrong in troubleshooting** (`operate/troubleshooting.md:54-60`): Omits `bound_plan` (`server/worker.py:246,322-375`; note `deployment_changed` was removed in `0cf17003`), and calls JSON field `path` (or `receiving_input`) `"input"` (`L56`).
- `[ ] FACT-25` **Undocumented operator footguns**:
  - `operate/admin-cli.md:24,98-103`: `lyra-admin` CLI defaults to HTTP (`secure=False`, `admin_cli.py:216`) while `LyraAdminClient`/`AsyncLyraAdminClient` default to HTTPS (`secure=True`, `sync.py:650`). Also `lyra-admin jobs list` requires `--status` (`admin_cli.py:165-169`).
  - `operate/configuration.md:70-72`: `mcp.mount_path` rejects trailing `/` (`config.py:234-236`, though clients call `/mcp/`); `api.public_base_url` rejects single-label Docker hostnames (`config.py:137-142`).
  - `operate/deployment.mdx:95-101`: `/lyra_data/` list omits `/lyra_data/secrets/` (created by both Dockerfiles and used in `operate/resources.md:31`). Also `examples/lyra-deployment/Dockerfile:26` still creates unused `/lyra_data/artifacts`.
  - `operate/configuration.md:82-84`: Link label `"Configure computation caching"` mismatches target `"Configure shared result storage"`, and operators don't *"enable persistent reuse"* in `lyra.toml` (cache policies are declared in plugin code).
  - `operate/configuration.md:88`: `config-summary` reports only the API process's subset of settings (`routes/admin.py:182-188`, returning `rq_retention_seconds`), not worker config.
  - ~~`operate/runbook.md:51`~~ — **Resolved in commit `20c90a3b`** (now accurately states `"Completed jobs: newest completion first, then descending job ID"`, backed by SQLite `terminal_statuses()`).
  - ~~`operate/updates.md:17-21` (`build_revision`)~~ — **Obsolete / resolved in commit `0cf17003`** (`build_revision` removed).
- `[ ] FACT-26` **Missing mandatory Pandera schema constraints**:
  - `plugins/location-providers.md:40-44`: Omits mandatory Pandera `checks` on `location_id` and mandatory `nullable=False` on `geometry` (`src/lyra/sdk/provider_schema.py:45-64`).
  - `plugins/outputs.md:79-84,211`: Omits that `coerce=True`, column `default=`, unnamed checks, and schema-level `dtype` are rejected at registration (`src/lyra/sdk/output_schema.py:65,152,212-223`), and that reserved columns (`location_id`, `row_key`, `source_location_id`) trigger registration errors when misused (`L235-240`).
- `[ ] FACT-27` **Missing signature & naming rules**:
  - `reference/parameters.md:15-21,147`: Handler signature table omits `Resource(...)` arguments (`sdk/handler.py:78-79`); naming rule omits location operation names (`sdk/plugin.py:274`).
  - `plugins/canonical-selection.md:20`: Omits two-positional-argument `(source, candidates)` signature when `target_selector` is used (`sdk/handler.py:226-241`).
  - `plugins/location-operations.md:43`: Implies all operation parameter models need defaults; only `canonical=True` operations require defaults on every field (`catalog.py:173-183`).
- `[ ] FACT-28` **Split-plugin caveat on `PluginDefinition.describe()` / `.implementation()`** (`plugins/authoring.md:157,174`): Raises `ValueError: Unknown provider` if a metric references a provider registered in a separate `PluginDefinition` (`sdk/plugin.py:589-590`).
- `[ ] FACT-29` **Stale/inaccurate `use/` details**:
  - `use/jobs.md:74`: Polling backoff cannot reach 30s in 5 retries (delays are ~1, 2, 4, 8, 16s; `polling.py:113-122`).
  - `use/mcp.md:88`: Inspection numeric bounds require declared dtype in `{"int64", "Int64", "float64", "Float64"}` and `count > 0` (`data/inspection.py:80-90`).
  - `use/mcp.md:126,180`: Lookup errors also include `input_name=null` (`retrieval_errors.py:42-46`).
  - `use/mcp.md:193-200`: Post-expiry tool table omits `lyra_download_location_input`.
  - `use/results.md:227-241`: Says *"All four exceptions inherit from `ResultAccessError`"*—omits 5th subclass `LocationInputUnavailableError` (`contracts/errors.py:164-171`), which isn't exported from `lyra.api`.
  - `use/results.md:338-343`: Full JSON result table omits `kind` and `status`; `L390` omits that `GET /jobs/{job_id}` also returns `500` `invalid_job_result` on damaged Valkey/SQLite records.
  - `use/rest-api.md:120`: *"result files"* → SQLite result store.
  - `use/local-execution.md:71`: Conflates provider resolution (preserves first-occurrence order, `runtime/locations.py:280-284`) with stored result tables (unordered).
- `[ ] FACT-30` **Tutorial factual nits & drift**:
  - `plugins/tutorial/first-metric.mdx:80`: Says `LocalRunner.run()` returns a dict (returns `RunResult`) and references an empty input dict not passed at `L61`.
  - `plugins/tutorial/testing.md:36-46`: Says `inspect_tutorial.py` prints node bindings (`node.inputs`), which the script never prints; never explains why `node.operation.mapping` prints `"executed"`. Also omits `uv run python -m city_example 7` advertised in `tutorial/index.md:63`.
  - `plugins/tutorial/canonical.md:89-111`: `test_empty_block_returns_typed_empty_streets()` never asserts `dtypes`.
  - `plugins/tutorial/select-streets.mdx:67`: Calls `STREETS` (`"city_streets"`) *"the declaration of the data it accepts"* rather than the provider ID string.
  - `plugins/tutorial/parameters.mdx:63`: Calls `metadata={"granularity": ...}` a schema declaration as if enforced (it's informational; `street_id` doesn't set `unique=True`).
  - `docs/src/components/MaintenanceDependenciesDiagram.astro:10-15`: Omits `Selected streets` input and draws arrowheads pointing into the side of the vertical bus line.
  - Hardcoded snippets in `select-streets.mdx:30-48`, `dependencies.mdx:64-89`, `blocks.mdx:50-70,89-119`, `canonical.md:15-36` drifted from unused `# docs:start` regions (`provider`, `maintenance`, `selector`) in `examples/city-plugin/`.
- `[ ] FACT-31` **Stale `tests/test_location_examples.py` description** (`plugins/location-operations.md:178`): Claims it tests *"reuse"* and *"constructed scopes"*; it tests neither.
- `[ ] FACT-32` **Descriptions abbreviation claim** (`plugins/descriptions.md:24`): Only MCP listings abbreviate descriptions (`server/mcp/tools.py:369-373`); HTTP `GET /metrics` returns unabbreviated descriptions.
- `[ ] FACT-33` **`plugins/index.md:26` wrong link & missing guides**: Points *"Supply data clients, settings, or credentials"* to `plugins/runtime/` (a stub) instead of `plugins/resources/`, and omits 8 of 17 standalone guides.
- `[ ] FACT-34` **Broken internal redirect in `use/local-execution.md:164`**: Links to `plugins/runtime/#define-an-io-resource` (a 1-line stub) instead of `plugins/resources/#define-an-io-resource`.
- `[ ] FACT-35` **`lyra-admin` inside `api` container relies on undeclared transitive `httpx`** (`operate/deployment.mdx:82` vs `operate/admin-cli.md:11`, `pyproject.toml:40-48`): Works today only via `mcp` → `httpx`. Either document or include `lyra[client]` in `examples/lyra-deployment/pyproject.toml`.

---

## Drift Introduced or Missed by Commit `20c90a3b` (SQLite Job Results Migration)

Commit `20c90a3b` moved terminal job status, structured failures, and job JSON results into SQLite indefinitely, renamed `jobs.result_retention_seconds` → `jobs.rq_retention_seconds`, removed `ResultLifetime` (`lifetime`), and made `GET /ready` return HTTP `200` (`degraded`) when SQLite is readable and the RQ broker (Redis at the time; Valkey as of `16ed7c65`) is down. The following doc locations still carry pre-migration claims:

- `[ ] FACT-36` **Stale *"retains jobs and results for a configured period"* claims**
  - **Docs:** `docs/src/content/docs/index.md:25` (*"The server retains jobs and results for a configured period."*) and `docs/src/content/docs/concepts/model.md:59` (*"The job runs the metric and its dependencies, then retains status and a terminal result for a configured period."*)
  - **Truth:** Terminal jobs (`succeeded` / `failed`), full results, descriptions, and reports are stored indefinitely in SQLite (`src/lyra/server/terminal_jobs.py:73-141`). Only live RQ records and admin tracebacks expire after `jobs.rq_retention_seconds` (`src/lyra/contracts/config.py:305-309`).
  - **Fix:** State that completed jobs and results are stored durably in SQLite (while live queue records and admin tracebacks expire after `jobs.rq_retention_seconds`).

- `[ ] FACT-37` **Stale `lifetime` claim on `client.results.inspect()`**
  - **Doc:** `docs/src/content/docs/use/python-client.md:113` — `| Inspect output metadata and lifetime | client.results.inspect(job_id) |`
  - **Truth:** `ResultLifetime` and the `lifetime` field were removed from `TerminalResultOutput` (`src/lyra/contracts/inspection.py:64-73`, `src/lyra/contracts/jobs.py:212`).
  - **Fix:** Change to `| Inspect output summaries and previews | client.results.inspect(job_id) |`.

- `[x] FACT-38` **Outdated `/ready` probe check misses `degraded` broker outages** — **Resolved for docs in commits `0cf17003` & `16ed7c65`**:
  - ~~`docs/src/content/docs/operate/updates.md:73`~~ — **Resolved in commit `0cf17003`** (`operate/updates.mdx:73` requires `` `GET /ready` returns HTTP 200 with `status` set to `ready`. ``).
  - ~~`docs/src/content/docs/operate/configuration.md:54`~~ — **Resolved in commit `16ed7c65`** (`operate/configuration.md:64` now explicitly instructs operators to check `valkey.status` is `ok` in `/ready`, alongside `operate/deployment.mdx:77` checking both `sqlite.status` and `valkey.status` and `operate/troubleshooting.md:30-31` splitting `degraded` vs HTTP `503`).
  - **Note:** `src/lyra/server/routes/health.py:86-98` returns HTTP `200` with `status: "ready"` when both SQLite and Valkey are healthy, HTTP `200` with `status: "degraded"` when SQLite is healthy but Valkey is unavailable, and HTTP `503` (`not_ready`) only when SQLite is unavailable. Both Compose healthchecks (`examples/lyra-deployment/compose.yml:25`, `docker/docker-compose.yml:26`) still check only HTTP `200`, so `--wait` passes during a Valkey outage (tracked in `FACT-41` & `UX-18`).

- `[ ] FACT-39` **Leftover *"expired job"* headings & obsolete post-queue-expiry framing**
  - **Docs:**
    - `docs/src/content/docs/reference/mcp-payloads.md:169`: Heading still says `## Unknown or expired job` (even though body at `L177` was updated to `"This job is unknown."`).
    - `docs/src/content/docs/operate/caching.md:31`: Still says *"Job expiry, worker restarts..."* (should be *"Queue expiry"*).
    - `docs/src/content/docs/use/location-exports.md:160`: Still says *"after transient jobs expire"*.
    - `docs/src/content/docs/use/results.md:380` (`## Keep access after queue expiry`) and `docs/src/content/docs/use/mcp.md:193` (`Use these tools after queue expiry`): Still frame `/results/*` and `/executions/*` as special post-expiry workarounds, even though terminal `/jobs/{job_id}` status, inspections, and JSON downloads now also survive queue expiry and Valkey outages via SQLite.
  - **Fix:** Update heading/wording and simplify the result-retrieval framing to distinguish endpoints by **payload format** (JSON vs Arrow table vs description vs report) rather than queue expiry. Also note in `use/results.md:390` that `500 invalid_job_result` can originate from corrupt SQLite terminal metadata (`src/lyra/server/terminal_jobs.py:86-91`) as well as missing stored outputs.

---

## Drift & Gaps After Commit `0cf17003` (`lyra-plugin dev`, `--config`, `build_revision` removal)

Commit `0cf17003` removed `build_revision` and `deployment_changed` (relying on accepted computation-plan checks → `bound_plan`), bumped `schema_version = 7`, deleted Docker Compose dev stacks in favor of `lyra-plugin dev`, rewrote `plugins/development.mdx` and `operate/updates.mdx`, and added `--config` to both `lyra.server.main` and `lyra.server.worker_launcher`. Remaining doc drift:

- `[ ] FACT-40` **Stale wording and omissions around `updates.mdx`, `--config`, and `lyra-plugin dev`**
  - `docs/src/content/docs/operate/configuration.md:17`: Still mentions only mounting at `/lyra_data/config/lyra.toml` without noting `--config` (`src/lyra/server/main.py:119`, `worker_launcher.py:143`).
  - `docs/src/content/docs/operate/configuration.md:88,90`: Still refers to `operate/updates.mdx` as *"the drain and restart procedure"* (it now stops services and empties waiting jobs with `rq empty`) and routes *"use an editable checkout"* through a 1-sentence stub in `operate/plugins.mdx:60` instead of linking directly to `plugins/development.mdx`.
  - `docs/src/content/docs/operate/runbook.md:36` & `docs/src/content/docs/plugins/development.mdx:26`: Link to legacy `<span id=>` anchors (`../updates/#drain-and-restart`, `../../quickstart/#set-your-connection-details`) rather than natural headings.
  - `docs/src/content/docs/plugins/development.mdx:62`: Links to `../../operate/resources/` (which still carries obsolete `FACT-18`), and doesn't note that `lyra-plugin dev` leaves MCP disabled (`mcp.enabled = False`, no `--mcp` flag in `src/lyra/sdk/dev.py:128-141`).
  - **Fix:** Update `operate/configuration.md` and `operate/runbook.md` links/wording, document `--config` in `operate/configuration.md`, and note MCP limitation + `source keys.env` convenience in `plugins/development.mdx`.

---

## Changes & Remaining Gaps After Commit `16ed7c65` (Valkey Backend & Private Dev Valkey)

Commit `16ed7c65` switched the RQ backend from Redis to Valkey (`[valkey]` / `ValkeyConfig`, `ValkeyHealth`, `schema_version = 8`, `valkey/valkey:9.2.0-alpine` in both Compose files, `unix:///` support alongside `redis://`/`rediss://`), updated `lyra-plugin dev` to spin up a private `valkey-server` on a temporary Unix socket in `/tmp/lyra-*/sock` by default (requiring `valkey-server` on `PATH`) with `--valkey-url` to connect to an external Valkey server, and updated all authored docs (resolving `FACT-38`; no stale Redis broker claims remain—only accurate `Redis Queue (RQ)` and `redis://`/`rediss://` references). Remaining omissions & caveats:

- `[ ] FACT-41` **Undocumented `valkey.url` / `--valkey-url` URL-scheme rules, Compose `--wait` healthcheck caveat, and `lyra-plugin dev` private-broker details**
  - `docs/src/content/docs/operate/configuration.md:56-62`, `docs/src/content/docs/plugins/development.mdx:32-40`, and `docs/src/content/docs/reference/generated/configuration.md:153-160`: Don't state that `valkey.url` and `--valkey-url` accept only `redis://`, `rediss://`, or three-slash absolute `unix:///` (`src/lyra/contracts/config.py:187-195`)—rejecting `valkey://`/`valkeys://` (because Lyra uses `redis-py`/RQ underneath) as well as `unix:/path` and `unix://relative`.
  - `examples/lyra-deployment/compose.yml:25` and `docker/docker-compose.yml:26`: Healthchecks only verify HTTP `200` on `/ready`, so `docker compose up --wait` (`operate/updates.mdx:66`) still passes during a Valkey outage (`status: "degraded"`).
  - `docs/src/content/docs/plugins/development.mdx:15-40`: Omits that the default private `valkey-server` binds an ephemeral Unix socket in `/tmp/lyra-*/sock` (`--port 0`, `--save ""`, `--appendonly no`, `src/lyra/sdk/dev.py:242-262,357-361`)—so there are no TCP port conflicts and live queue state is not persisted across restarts (only SQLite results and `keys.env` persist in the cache dir)—and that `--valkey-url` also accepts `rediss://` and `unix:///` (with a 10s readiness probe in `src/lyra/server/dev_queue.py:22-30` before discarding waiting jobs).
  - **Fix:** Document the exact accepted URL schemes (`redis://`, `rediss://`, `unix:///`, and why `valkey://` is rejected) in `operate/configuration.md` and `plugins/development.mdx`, note the ephemeral Unix-socket private broker behavior in `plugins/development.mdx`, and update or document the Compose `/ready` healthcheck `status == "ready"` caveat (`UX-18`).
