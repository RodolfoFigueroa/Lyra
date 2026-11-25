# Ledger 03: Information Architecture Consolidation Plan

Target: reduce **66 authored pages → ~34 focused authored pages** (+ 4 slimmed generated reference pages + scoped public Python API). Every topic gets **one canonical home**.

---

## Cross-Page Duplication Inventory to Eliminate

| Duplicated topic | Currently repeated across | Single canonical home |
| --- | --- | --- |
| Saved descriptions / Arrow tables / location inputs / execution reports | `use/python-client.md`, `use/rest-api.md`, `use/mcp.md`, `use/results.md`, `use/location-exports.md`, `use/local-execution.md:240` | `use/results.md` (other `use/*` pages show happy path + link) |
| Atomic file writes & temp cleanup | Repeated 8+ times across `use/python-client.md`, `use/results.md`, `use/location-exports.md` | One short callout in `use/results.md` |
| Mutation isolation (*"editing cannot change stored data"*) | Repeated 11+ times across `use/*`, `plugins/*`, `concepts/*`, `reference/*` | State once in `concepts/how-lyra-works.md` & `use/results.md` |
| Revision invalidation rules (`code_revision` vs `origins`; `build_revision` removed in `0cf17003`) | `concepts/identity.md`, `concepts/caching.md`, `operate/caching.md`, `operate/updates.mdx`, `operate/runbook.md`, `plugins/authoring.md`, `plugins/outputs.md`, `plugins/location-providers.md` | One 2-row table in `concepts/caching-and-revisions.md` + link everywhere else |
| Verbatim 7-line transaction-atomicity paragraph | `concepts/graphs.md:70-76`, `concepts/caching.md:114-120`, `operate/caching.md:37-43` | Keep once in `concepts/caching-and-revisions.md` |
| `city_street_maintenance` dependency graph walkthrough | `plugins/tutorial/dependencies.mdx` AND `plugins/dependencies.mdx:13-83` | Tutorial teaches it; `plugins/dependencies.md` is a concise How-to |
| Block-to-street operation tutorial | `plugins/tutorial/blocks.mdx` AND `plugins/location-operations.md` (and `location-example.mdx` blocks/lots) | Tutorial teaches it; `plugins/location-operations.md` is a concise How-to; delete `location-example.mdx` |
| Plugin packaging & `lyra-plugin describe`/`diagram` | `plugins/tutorial/package.md`, `plugins/quickstart.mdx`, `plugins/publish-and-debug.mdx` | Delete `plugins/quickstart.mdx`; keep short Ch. 6 finish in tutorial + complete How-to in `plugins/packaging.md` |
| `FakeCityProvider` + `resource_overrides` failure test | `plugins/tutorial/testing.md:132-171` AND `plugins/testing.md:53-112` | Brief intro in tutorial; complete patterns (`BoundPlugin`, Hypothesis, `tmp_path`) in `plugins/testing.md` |
| `lyra-admin` commands & offline config validation | `operate/deployment.mdx`, `operate/configuration.md`, `operate/runbook.md`, `operate/admin-cli.md`, `operate/troubleshooting.md`, `reference/generated/cli.md` | `operate/monitoring.md` + `reference/generated/cli.md` |
| 1-sentence redirect H2 stubs | `operate/configuration.md:28,72`, `operate/plugins.mdx:60`, `operate/runbook.md:87`, `plugins/runtime.md:52-66` (4 stubs), `plugins/location-operations.md:180-187` (2 stubs), `plugins/authoring.md:118-120` | Delete all 11 stub sections |

---

## Page-by-Page Disposition Map (All 66 Authored Pages)

### 1. Start Here (5 → 4 pages)
- `[ ] IA-01` **Keep & tighten** `index.md` (44L) — absorb the local-vs-server comparison table from `start/choose-execution.md`.
- `[ ] IA-02` **Delete / merge** `start/choose-execution.md` (41L) → fold concise comparison into `index.md` (fix `FACT-01`; note `20c90a3b` already simplified `L39` to reflect durable SQLite terminal job storage).
- `[ ] IA-03` **Keep & tighten** `start/installation.md` (71L).
- `[ ] IA-04` **Keep & simplify** `start/local.mdx` (58L) — rename script to `try_local.py` (no Ch. 1 collision), cut `.report`/`.metadata` internals.
- `[ ] IA-05` **Keep & clean** `quickstart.mdx` (111L) — strip legacy `<span id=>` anchors.

### 2. Run Metrics — `use/` (8 → 6 pages)
- `[ ] IA-06` **Keep & trim** `use/local-execution.md` (240L) — remove stray tail paragraph (`L240`), fix imports & `Path` requirement, link directly to `plugins/resources.md`.
- `[ ] IA-07` **Keep & trim** `use/python-client.md` (367L → ~200L) — happy path first (connect → discover → submit/wait → `result_tables`), cut ~110L of duplicated retained-store/location-input/export prose, remove stray stubs (`L354,358`).
- `[ ] IA-08` **Keep & rewrite** `use/rest-api.md` (202L → ~140L) — add runnable `curl` commands, add `/executions/{job_id}/report/inspection`, cut duplicated Arrow/retained/error tables (link to `results.md` + `reference/generated/http.md`).
- `[ ] IA-09` **Keep & refocus** `use/mcp.md` (237L → ~140L) — focus on wiring an AI assistant + 4-step workflow; cut unnumbered reference sections (`L104-237`) that duplicate `reference/generated/mcp.md` and `use/results.md`.
- `[ ] IA-10` **Keep & clarify** `use/spatial-inputs.md` (76L) — define `interventions` clearly, fix `FACT-07`, remove hardcoded GitHub `main` link.
- `[ ] IA-11` **Keep (slim) OR merge** `use/jobs.md` (94L) — keep polling/retry/error recovery in one place (remove duplicates from `python-client.md`), fix `FACT-04`, `FACT-05`, `FACT-29`.
- `[ ] IA-12` **Restructure & trim** `use/results.md` (406L → ~240L) — single source of truth for result retrieval; invert order so decision matrix + `result_tables()` come first; absorb essential GeoParquet export section from `location-exports.md`.
- `[ ] IA-13` **Delete / merge** `use/location-exports.md` (179L) → fold concise GeoParquet export section (~35L) into `use/results.md`; drop 3 repetitive wrapper snippets.

### 3. Build Plugins — Tutorial `plugins/tutorial/` (9 → 7 pages)
- `[ ] IA-14` **Keep & update** `plugins/tutorial/index.md` (67L) — update chapter map for 6 chapters; remove `uv sync` duplication where possible.
- `[ ] IA-15` **Rewrite** `plugins/tutorial/first-metric.mdx` (106L) — start in `city_example/tutorial.py`, trim unexplained concepts, fix `FACT-30`.
- `[ ] IA-16` **Tighten** `plugins/tutorial/select-streets.mdx` (125L) — use `# docs:start provider`, explain `resources=` registration.
- `[ ] IA-17` **Tighten** `plugins/tutorial/parameters.mdx` (138L) — fix Pint `"score"` (`FACT-11`), stay in `tutorial.py`.
- `[ ] IA-18` **Tighten** `plugins/tutorial/dependencies.mdx` (157L) — fix `pytest -n auto` (`FACT-09`), use `# docs:start maintenance`.
- `[ ] IA-19` **Rebalance Ch. 5 + Ch. 6**:
  - Turn `plugins/tutorial/blocks.mdx` (220L cliff) + `plugins/tutorial/canonical.md` (120L for a 1-line `canonical=True` change) into **two balanced `.mdx` chapters**:
    - **Ch. 5 (`select-blocks.mdx` or `canonical.mdx`)**: Map blocks to streets with `@location_operation(..., canonical=True)` and run `city_street_maintenance` with block IDs.
    - **Ch. 6 (`blocks.mdx`)**: Add `street_membership` + weighted `city_block_maintenance` summary (with fixed `BlockWorkloadDiagram.astro`, `FACT-10`, `FACT-12`).
- `[ ] IA-20` **Trim & merge or keep slim** `plugins/tutorial/testing.md` (182L) + `plugins/tutorial/package.md` (105L) → either keep as Ch. 6 & Ch. 7 (if 5+6 stay separate, making 7 chapters) or trim Ch. 7 (`testing.md`) sharply (drop `runner.inspect()` deep dive, add real sample output, fix `FACT-30`) and convert both to `.mdx` with `<Steps>` to match Ch. 1–5.

### 4. Build Plugins — Standalone Guides `plugins/` (18 → 10 pages)
- `[ ] IA-21` **Rewrite** `plugins/index.md` (31L) — list all remaining standalone guides accurately, fix `FACT-33`.
- `[ ] IA-22` **Refocus & merge** `plugins/authoring.md` (187L) + `plugins/metrics.md` (80L) → single `plugins/authoring.md` (*Write and register metrics*): add complete `@metric` handler, metric factory pattern, `private=True`, `code_revision`, `origins`; cut parameter-spec & cache-key dumps (link to `reference/parameters.md` & `concepts/caching-and-revisions.md`); move output `meaning`/`granularity` to `outputs.md`. **Delete `plugins/metrics.md`.**
- `[ ] IA-23` **Refocus** `plugins/outputs.md` (232L) — add concrete `primary=True` & multi-output examples; fix `FACT-26`; cut SQLite internals & Hypothesis dupes.
- `[ ] IA-24` **Fold or rewrite** `plugins/descriptions.md` (59L) — either fold key bullets + before/after Python example into `plugins/authoring.md`, or keep standalone only after adding concrete code examples and stripping agent-skill rubric prose.
- `[ ] IA-25` **Refocus & convert to `.md`** `plugins/dependencies.mdx` (186L → `dependencies.md`) — cut city-tutorial rebuild (`L15-83`), fix `FACT-13` & `FACT-15`, focus on `Dependency`, `.bind()`, parameter forwarding, `lyra-plugin diagram`.
- `[ ] IA-26` **Merge & convert to How-to** `plugins/location-operations.md` (196L) + `plugins/canonical-selection.md` (64L) → single `plugins/location-operations.md` (*Derive and route locations*): How-to with concrete `@location_operation`, `canonical=True`, `route_preferences`, caching; delete 4 redirect stubs; fix `FACT-15`, `FACT-27`, `FACT-31`. **Delete `plugins/canonical-selection.md`.**
- `[ ] IA-27` **Delete** `plugins/location-example.mdx` (294L) — move concise inline `LocationProvider` + two-plugin snippet into `plugins/location-providers.md`.
- `[ ] IA-28` **Rewrite as How-to** `plugins/location-providers.md` (193L) — reclassify `Reference` → `How-to`, add concrete `LocationProvider`, `ParquetLocationProvider`, and `PostGISLocationProvider` Python examples, fix `FACT-14` & `FACT-26`, trim duplicated lifecycle/revision prose.
- `[ ] IA-29` **Merge** `plugins/resources.md` (193L) + `plugins/runtime.md` (66L, 4 stubs) → `plugins/resources.md` (*Resources and runtime context*): covers `BaseIO`, `PostgresIO`, and `RunContext` (`logger`, `temp_dir`, `report_progress`). Fix import-time Parquet write in `resource_example.py` and show `LocalRunner(..., resource_config=...)`. **Delete `plugins/runtime.md`.**
- `[ ] IA-30` **Keep & structure** `plugins/earth-engine.md` (62L) — add H2 headings and a complete `@metric` snippet (or fold as a subsection of `plugins/resources.md`).
- `[ ] IA-31` **Keep & improve** `plugins/testing.md` (142L) — add `result_store_path=tmp_path / "results.sqlite3"`, drop unexplained repo-internal *"service-access guard"* claim, fix `L106` dead `.tables`.
- `[ ] IA-32` **Merge & rename** `plugins/publish-and-debug.mdx` (61L) + `plugins/quickstart.mdx` (48L) → `plugins/packaging.md` (*Package and inspect a plugin*): add minimal from-scratch `pyproject.toml` + layout template, entry points, `lyra-plugin describe`/`diagram`, operator handoff. **Delete `plugins/quickstart.mdx` and `plugins/publish-and-debug.mdx`.**
- `[ ] IA-33` **Keep & polish** `plugins/development.mdx` (52L, *Run a local plugin server*) — Commit `0cf17003` replaced the old Docker Compose editable-mount guide with the native `lyra-plugin dev` CLI guide, so it now genuinely belongs in `Build Plugins`! Strip `<span id="develop-with-editable-sources">`, fix `FACT-40` (broken link to `operate/resources/`, note MCP is disabled in `dev`, mention `source keys.env` & cache directory).
- `[ ] IA-34` **Keep & clean** `plugins/agent-assisted-authoring.mdx` (115L) — fix `FACT-16`, strip `<span id=>` anchors.

### 5. Deploy and Operate — `operate/` (10 → 6 pages)
- `[ ] IA-35` **Merge** `operate/requirements.md` (43L) → *"Before you start"* section at top of `operate/deployment.mdx`. **Delete `operate/requirements.md`.**
- `[ ] IA-36` **Rewrite** `operate/deployment.mdx` (107L) — reconcile root `Dockerfile`/`docker/docker-compose.yml` vs `examples/lyra-deployment/`, fix step order, add bare-metal `--config` launch commands & `/lyra_data/secrets/` layout.
- `[ ] IA-37` **Expand & consolidate** `operate/configuration.md` (84L) ← absorb `operate/resources.md:11-36` (TOML resource config & secrets + add PostGIS example; **delete** wrong `resources.md:38-42` `FACT-18`) and operator storage/SQLite settings from `operate/caching.md` (`result_store_path`, single-host/no-NFS warning, online backups, growth planning; **delete** verbatim copy-paste `L37-43` and false pagination `FACT-19`). Document `--config`, fix `FACT-38` (`/ready` `ready` vs `degraded`) & `FACT-40`. Move Python embedding (`create_app`/`launch_worker`) out of operator config. Add minimal complete `lyra.toml` (`schema_version = 7`) and nginx/Caddy reverse-proxy snippet. **Delete `operate/resources.md` and `operate/caching.md`.**
- `[ ] IA-38` **Keep & tighten** `operate/plugins.mdx` (73L) — production image plugin installation & multi-provider setup; delete 1-sentence stub (`L60`, linking cleanly to `plugins/development.mdx` for local dev).
- `[ ] IA-39` **Merge** `operate/runbook.md` (89L) + `operate/admin-cli.md` (112L) → `operate/monitoring.md` (*Monitor and administer a deployment*): health (`/ready` `ready`/`degraded`/`not_ready` SQLite+Redis semantics from `20c90a3b`), logs (`stderr` `FACT-20`), `lyra-admin` & `LyraAdminClient` (with `secure=True` & `--status` notes `FACT-25`), RQ interruption recovery & `jobs.rq_retention_seconds`, SQLite terminal history during Redis outages. Delete stub (`runbook.md:87-89`) and fix `L36` anchor (`FACT-40`). **Delete `operate/runbook.md` and `operate/admin-cli.md`.**
- `[ ] IA-40` **Keep & tighten** `operate/updates.mdx` (78L, already converted to `.mdx` + `<Steps>` and `build_revision` removed in `0cf17003`) — cut duplicated revision prose to a 2-sentence summary + link, strip `<span id=>` anchors (`L29,59`).
- `[ ] IA-41` **Keep & fix** `operate/troubleshooting.md` (75L) — fix `FACT-05`, `FACT-21`, `FACT-24` (`bound_plan`, no `deployment_changed`); keep strictly symptom → diagnosis → fix.

### 6. Understand Lyra — `concepts/` (8 → 5 pages, reordered big-picture first)
- `[ ] IA-42` **Merge & move to #1**: `concepts/model.md` (63L) + `concepts/architecture.md` (94L) → `concepts/how-lyra-works.md` (*How Lyra works*: metrics, providers, plugins, local vs server architecture, accepted-plan validation, component boundaries; fix `FACT-03`, `FACT-36`).
- `[ ] IA-43` **Merge as #2**: `concepts/graphs.md` (76L) + `concepts/selection.md` (27L stub) → `concepts/graphs.md` (*Dependency graphs and location routing*: add routing diagram, fix `FACT-02`, delete copy-pasted `L70-76`).
- `[ ] IA-44` **Keep as #3**: `concepts/lifecycle.md` (72L, now titled *How jobs execute and complete*) — *Jobs, timeouts, and terminal history* (accepted computation-plan verification → `bound_plan`, SQLite durable terminal history + Redis live queue/`jobs.rq_retention_seconds`).
- `[ ] IA-45` **Merge as #4**: `concepts/identity.md` (57L) + `concepts/caching.md` (147L) → `concepts/caching-and-revisions.md` (*Revisions, interventions, and caching*: discovery fingerprint vs computation identity, concrete examples of what breaks if you don't bump `code_revision` or `origins`, 2-row revision table, overlapping selections example, trim storage-engine internals).
- `[ ] IA-46` **Rewrite as #5**: `concepts/provenance.md` (164L → ~90L) — open with one small concrete `ExecutionReport` example, cut defensive negative specs, clearly separate saved `ExecutionReport` from transient `runner.inspect()`.

### 7. Reference — `reference/` (8 → 6 authored + 4 slimmed generated)
- `[ ] IA-47` **Keep & update** `reference/index.md` (47L) — match new page list.
- `[ ] IA-48` **Expand** `reference/python.md` (33L) — add `location_operation`, `RunContext`, `LyraAdminClient`, `AsyncLyraAdminClient`, `JobHandle`, `RunOptions`, `lyra.utils`; fix `Input` link (`TOOL-03`).
- `[ ] IA-49` **Keep & fix** `reference/parameters.md` (149L) — add `Resource(...)` arguments & operation naming (`FACT-27`).
- `[ ] IA-50` **Keep & trim** `reference/target-selection.md` (193L) — keep strong `L18-144`, trim internal staging/invariant notes (`L154-193`).
- `[ ] IA-51` **Keep** `reference/units.md` (29L) — fix UK spellings.
- `[ ] IA-52` **Merge** `reference/locations.md` (10L stub) → `reference/location-artifacts.md` (86L, rename *"Native tables and GeoParquet exports"*). **Delete `reference/locations.md`.**
- `[ ] IA-53` **Condense** `reference/mcp-payloads.md` (385L → ~150L) — canonical end-to-end payloads only, CI-validated against Pydantic models (`TOOL-08`).

---

## Target `docs/navigation.json` Structure (~34 Authored Pages)

```text
Start Here (4)
  ├── What is Lyra? (index) [includes local-vs-server table]
  ├── Install Lyra for Python (start/installation)
  ├── Run your first metric locally (start/local)
  └── Run your first server job (quickstart)

Run Metrics (6)
  ├── Run plugins locally (use/local-execution)
  ├── Use the Python client (use/python-client)
  ├── Use the HTTP API (use/rest-api)
  ├── Connect an AI assistant with MCP (use/mcp)
  ├── Select locations and interventions (use/spatial-inputs)
  ├── Monitor jobs and handle failures (use/jobs)
  └── Read, download, and export results (use/results)  [note: 7 if jobs stays separate, 6 if merged]

Build Plugins (17 = 1 landing + 6–7 tutorial + 10 guides)
  ├── Choose an authoring guide (plugins/index)
  ├── Build a city plugin (plugins/tutorial/* — 6–7 balanced .mdx chapters)
  ├── Write and register metrics (plugins/authoring)
  ├── Declare output tables and units (plugins/outputs)
  ├── Connect metrics and dependencies (plugins/dependencies)
  ├── Derive and route locations (plugins/location-operations)
  ├── Define location providers (plugins/location-providers)
  ├── Resources and runtime context (plugins/resources)
  ├── Use Earth Engine in metrics (plugins/earth-engine)
  ├── Test a plugin (plugins/testing)
  ├── Run a local plugin server (plugins/development)
  ├── Package and inspect a plugin (plugins/packaging)
  └── Use agent-assisted authoring (plugins/agent-assisted-authoring)

Deploy and Operate (6)
  ├── Deploy Lyra (operate/deployment) [includes requirements]
  ├── Configure Lyra, resources, and storage (operate/configuration)
  ├── Install plugins in Docker (operate/plugins)
  ├── Monitor and administer a deployment (operate/monitoring)
  ├── Update a deployment safely (operate/updates)
  └── Troubleshoot a deployment (operate/troubleshooting)

Understand Lyra (5)
  ├── How Lyra works (concepts/how-lyra-works)
  ├── Dependency graphs and location routing (concepts/graphs)
  ├── Jobs, timeouts, and terminal history (concepts/lifecycle)
  ├── Revisions, interventions, and caching (concepts/caching-and-revisions)
  └── Execution reports and provenance (concepts/provenance)

Reference (6 + 4 generated + scoped Python API)
  ├── Find a contract (reference/index)
  ├── Python API overview (reference/python)
  ├── Parameter and handler rules (reference/parameters)
  ├── Target selection rules (reference/target-selection)
  ├── Output column units (reference/units)
  ├── Native tables and GeoParquet exports (reference/location-artifacts)
  ├── MCP payload examples (reference/mcp-payloads)
  ├── Generated contracts (cli, configuration, http, mcp)
  └── Python API (scoped starlight-pydocs)
```
