# Ledger 04: First-Time User Onboarding & Section Rewrite Briefs

Concrete user-experience fixes and missing essentials for each area of `docs/`.

---

## 1. Start Here (`index.md`, `start/*`, `quickstart.mdx`)

- `[ ] UX-01` **Rename `start/local.mdx` script to avoid Ch. 1 collision:** `start/local.mdx:28` creates `first_metric.py` inside `examples/city-plugin/`, then links straight to `plugins/tutorial/`, where Ch. 1 (`first-metric.mdx:34`) creates a completely different `first_metric.py` in the same folder. Rename `start/local.mdx`'s script to `try_local.py`.
- `[ ] UX-02` **Trim premature internals from `start/local.mdx:50`:** Remove *"native tables in `.tables` and execution details in `.metadata`. It also attaches `.report` before returning, so you can inspect the execution without another lookup."* Keep the tutorial focused on running a metric and reading its DataFrame.
- `[ ] UX-03` **Lead `index.md` with metrics, plugins, and local execution (keep Valkey out of Start Here):** Absorb `start/choose-execution.md` cleanly and fix `index.md:25` (`FACT-36`: completed jobs and results stay saved on the server, not for a configured expiry period). A local run never touches Valkey, and server consumers never interact with Valkey—mention Valkey at most once in a brief *"Operating a server"* note pointing to `operate/`.

---

## 2. Run Metrics (`use/*`)

- `[ ] UX-04` **Frame `use/results.md` around output formats, not Valkey or queue expiry:** After commit `20c90a3b`, completed job statuses, full JSON results (`/jobs/{job_id}/result*`), inspections, Arrow tables, location inputs, descriptions, and reports all stay saved indefinitely on the server. Server consumers never need to know Valkey exists or understand queue expiry.
  - Put a crisp **format/method decision table** at the top of `use/results.md` (DataFrames via `client.results.tables()`/`table()`, full JSON via `get()`/`download()`, preview via `inspect()`, metadata via `describe()`, provenance via `reports.get()`, local file via `ResultStore`).
  - Keep Valkey out of `use/*` and delete repetitive `"even after queue expiry"` disclaimers across `use/*` (`FACT-39`); talk purely in user-facing terms (*active jobs* vs *completed jobs*).
- `[ ] UX-05` **Invert `use/results.md` so the happy path comes first:** Currently lines 11–149 cover individual saved metadata/tables/reports and `client.result_tables(job_id)` is buried at line 310. Lead with: (1) format/method decision matrix, (2) getting DataFrames (`tables` / `table`), (3) inspecting descriptions & reports, (4) location inputs & GeoParquet exports, (5) reopening local SQLite results, (6) row identity, CRS, and durability.
- `[ ] UX-06` **Fix missing imports and non-runnable snippets:**
  - `use/local-execution.md:224-230`: Give exact import paths for all 8 listed exceptions (`PreparationError`, `MetricInputError`, `PluginDefinitionError`, `GraphExecutionError`, `UnresolvedLocationIDsError`, `LocationAcquisitionError`, `LocationProviderContractError`, `ExecutionPersistenceError`).
  - `use/local-execution.md:193`: Warn that `LocalRunner(..., result_store_path=...)` requires a `pathlib.Path` (passing `str` raises `AttributeError` at `src/lyra/runtime/local.py:153`), unlike `ResultStore.open()`.
  - `use/local-execution.md:110-120` & `L170`: Make snippets self-contained (don't reference undefined `proposal` or `plugin`).
  - `use/python-client.md:23,63`: Remove unused `JsonObject` import; don't append a second `with LyraClient(...)` block to the same script.
  - `use/python-client.md:210` & `use/results.md:277`: State that `LocationInputUnavailableError` is imported from `lyra.contracts.errors` (not `lyra.api`).
  - Explain `lyra[client,tables]` vs `lyra[client,data]` once consistently (stop alternating between them arbitrarily; note `lyra[tables]` already includes `pyarrow`).
  - Replace abstract untyped export wrapper functions (`use/location-exports.md:34-78`) with one concrete spatial example (noting `export_geoparquet` requires a spatial table, so it raises `ValueError` on nonspatial `maintenance`).
- `[ ] UX-07` **Make `use/rest-api.md` and `use/mcp.md` practical:**
  - `use/rest-api.md`: Add runnable `curl` commands (currently only bare paths and JSON fragments) and include `GET /executions/{job_id}/report/inspection`.
  - `use/mcp.md`: Add a real MCP client configuration snippet (endpoint URL + Bearer header) and a brief prompt-to-tool walkthrough instead of reading purely like a protocol wire spec.

---

## 3. City Plugin Tutorial (`plugins/tutorial/*`)

- `[ ] UX-08` **Eliminate throwaway-file churn & git checkout dirtying:**
  - Currently Ch. 1–3 create 3 root scripts (`first_metric.py`, `selected_metric.py`, `maintenance_metric.py`) that are abandoned in Ch. 4 yet cannot be deleted because tests import them, creating ~13 untracked files alongside pre-built `city_example/{metrics,plugin,checkpoints}.py` and overwriting git-tracked `city_example/__init__.py` in Ch. 8.
  - Start in `city_example/tutorial.py` from Chapter 1 (or use a single clean runner pattern) with `create_plugin()` consistently (Ch. 3 currently drops the factory pattern for a bare `plugin = PluginDefinition(...)` variable, then Ch. 4 switches back).
  - Guard all executable scripts with `if __name__ == "__main__":` so importing them in pytest doesn't execute runs and mutate `.lyra/results.sqlite3` at import time.
  - Warn before overwriting `city_example/__init__.py` (or use a clean tutorial project copy).
- `[ ] UX-09` **Flatten complexity cliffs & rebalance chapters:**
  - **Ch. 1 (`first-metric.mdx`):** Don't highlight `code_revision="1"` or `origins=(CITY_ORIGIN,)` without explaining them; briefly explain that `column(...)` is a local helper in `city_example/schemas.py` (not a Lyra built-in) and what the `{"city": ...}` key in `LocalRunner.from_definitions(...)` means.
  - **Ch. 2 (`select-streets.mdx`):** Explain why `LocationProvider` is registered under `resources=` rather than a `providers=` argument, and explain `with self.operation("resolve"):` if highlighted (or omit highlighting it).
  - **Ch. 5 (`blocks.mdx`, 220L) + Ch. 6 (`canonical.md`, 120L for 1 line):** Split Ch. 5 and fold Ch. 6 into two smooth chapters: (a) map blocks → streets with `@location_operation(..., canonical=True)` and immediately run `city_street_maintenance` with block IDs; (b) add `street_membership` + weighted `city_block_maintenance`. Briefly show `CityBlockProvider`'s `street_records`/`allocations` columns before reading them, and explain why unused `parameters: MaintenanceParameters` is declared on `block_maintenance` (to expose `condition_threshold` in its public schema).
  - **Ch. 7 (`testing.md`):** Show **concrete sample output** for `inspect_tutorial.py`. Drop the `runner.inspect()` deep dive (two overlapping vocabularies `"executed"` vs `"completed"` confuse beginners); keep `result.report` + `resource_overrides` failure testing. Explain `.lyra/results.sqlite3` before telling readers to *"Save the store path"*, and don't reference the repo-internal *"service-access guard"* without explaining `tests/conftest.py`.
- `[ ] UX-10` **Redraw both Astro diagrams:**
  - `BlockWorkloadDiagram.astro`: Fix wrong topology (`FACT-10`: `Selected blocks` fans out directly to `streets_for_blocks`, `street_membership`, and `block_maintenance`).
  - `MaintenanceDependenciesDiagram.astro`: Add `Selected streets` input and fix arrowheads pointing into the vertical bus line (`FACT-30`).

---

## 4. Standalone Plugin Guides (`plugins/*`)

- `[ ] UX-11` **Add a "Start a new plugin from scratch" template:** Nowhere in the standalone guides is there a complete minimal `pyproject.toml` (`[build-system]`, `dependencies = ["lyra[sdk]"]`, `[dependency-groups] dev = ["lyra[runtime]", "pytest"]`, `[project.entry-points."lyra.plugins"]`) and package directory tree. Add this to `plugins/packaging.md` (and link from `plugins/index.md` & `plugins/authoring.md`).
- `[ ] UX-12` **Add missing concrete code examples to prose-only guides:**
  - `plugins/location-providers.md`: Show a minimal custom `LocationProvider` subclass (with Pandera `location_id` nonempty check + non-null `geometry`), plus short `ParquetLocationProvider` and `PostGISLocationProvider` examples.
  - `plugins/authoring.md`: Show one complete `@metric` combining `MetricParameters`, `LocationDependency`, and `outputs`.
  - `plugins/outputs.md`: Show a location-scoped (`primary=True`, `location_id`, `row_key`, composite uniqueness) metric and a multi-output (`dict[str, pd.DataFrame]`) metric.
  - `plugins/location-operations.md` (after absorbing `canonical-selection.md`): Show `canonical=True` and `route_preferences=[...]`.
  - `plugins/earth-engine.md`: Show a full `@metric` consuming `EarthEngineIO` via `Annotated[EarthEngineIO, Resource(...)]`.
  - `plugins/resources.md`: Show running `observations` via `LocalRunner.from_definitions(..., resource_config=...)`, fix `directory = "/data"` vs `example-data` mismatch, and move the Parquet fixture creation out of module import scope.
  - `plugins/dependencies.md`: Print `tables["maintenance"]` in `maintenance_graph.py` so running the script produces visible output.
- `[ ] UX-13` **Unify the three conflicting synthetic cities:** Docs currently mix:
  1. `examples/city-plugin` (`city_streets`/`city_blocks`, `street-1`/`block-1`, EPSG:32614)
  2. `examples/location-plugin/location_plugin/graph.py` (**same** provider IDs `city_streets`/`city_blocks`, but `s1`/`b1`, EPSG:3857)
  3. `plugins/location-example.mdx` (`example_blocks`/`example_lots`, `block-a`/`lot-x`, EPSG:4326)
  Deleting `location-example.mdx` removes #3; align standalone guide snippets to `city-plugin` conventions (or clearly distinguish `location-plugin` when showcasing managed PostGIS/Parquet queries).
- `[ ] UX-14` **Improve `plugins/testing.md`:** Document `LocalRunner(..., result_store_path=tmp_path / "results.sqlite3")` so tests don't pollute `.lyra/results.sqlite3`, and remove the assumption that external plugin authors have the repo-internal `no_external_services` fixture (`plugins/testing.md:111`).
- `[ ] UX-15` **Add missing imports across plugin snippets:** `JsonValue` (`pydantic`), `Origin`/`RunContext`/`Resource` (`lyra.sdk`), `PluginDefinitionError`/`MetricResultError`/`LocationAcquisitionError`/`LocationProviderContractError` (`lyra.contracts.errors`), `LocalRunner` (`lyra.runtime.local`).
- `[ ] UX-15b` **Polish `plugins/development.mdx` (`lyra-plugin dev`):** Commit `16ed7c65` switched `lyra-plugin dev` to launch an ephemeral private `valkey-server` on a temporary Unix socket (`/tmp/lyra-*/sock`, no TCP port conflict, requires `valkey-server` on `PATH`) by default, with `--valkey-url` to connect to an external Valkey server. Explain that `keys.env` can be sourced directly (`set -a; source ...; set +a`), state where the cache lives (`$XDG_CACHE_HOME/lyra/lyra-dev-*` or `~/.cache/lyra/lyra-dev-*`, persisting keys and SQLite results while the private Valkey queue is ephemeral), clarify accepted `--valkey-url` schemes (`redis://`, `rediss://`, `unix:///`; `FACT-41`), note that `lyra-plugin dev` runs with MCP disabled (`mcp.enabled=False`), and fix its link to `operate/resources/` (once merged into `operate/configuration.md`).

---

## 5. Deploy and Operate (`operate/*`)

- `[ ] UX-16` **Reconcile root `Dockerfile`/`docker/docker-compose.yml` vs `examples/lyra-deployment/`:** Commits `0cf17003` and `16ed7c65` deleted `docker/docker-compose-dev.yml` and switched both Compose stacks and `config.example.toml` to Valkey (`valkey/valkey:9.2.0-alpine`), but `operate/` still ignores root `Dockerfile`, `config.example.toml`, `.env.example`, and `docker/docker-compose.yml` (different service names `lyra`/`lyra-valkey` vs `api`/`valkey`, no plugins installed, no `stop_grace_period` on workers). Clarify what the root files are for (base image) vs `examples/lyra-deployment/` (production deployment template), or consolidate them.
- `[ ] UX-17` **Fix `operate/deployment.mdx` step sequence:** *(Note: `0cf17003` removed `build_revision` from Step 4.)*
  - Briefly explain the minimal `lyra.toml` structure before Step 1 asks operators to edit `api.public_base_url`.
  - List `uv` as a prerequisite if validating offline on the host, or show validating inside the container.
  - Remind operators to `export LYRA_ADMIN_API_KEY=...` when switching from `docker compose exec api lyra-admin` to host `uv run lyra-admin`.
- `[ ] UX-18` **Fill critical production deployment gaps:**
  - Include a **minimal complete `lyra.toml`** (`schema_version = 8`, with `[valkey]`) in `operate/configuration.md`.
  - Document **non-Docker / bare-metal launch commands** (`python -m lyra.server.main --config ...` and `python -m lyra.server.worker_launcher --config ...`, defaulting to `/lyra_data/config/lyra.toml` as added in `0cf17003`).
  - Add a **concrete reverse-proxy snippet** (nginx or Caddy) and warn that MCP DNS-rebinding protection (`src/lyra/server/mcp/server.py:238-242`) rejects mismatched `Host` headers if `Host` does not match `api.public_base_url`.
  - Add a **realistic PostGIS resource configuration example** in `[resources.*]` (currently only fictional `shared_files` + `remote` token).
  - Add an explicit **SQLite single-host / no-NFS warning** and safe **online backup** + **scratch-dir cleanup** (`/lyra_data/cache/jobs` / `worker.temp_dir`) instructions. Emphasize that `/lyra_data/results.sqlite3` now holds **durable terminal job history** as well as cached results and reports, and drives `/ready` HTTP `503` readiness.
  - Add a **Valkey eviction, URL-scheme, degraded-mode, & auth/TLS (`rediss://`) note**: warn against `allkeys-lru` evicting active RQ keys, clarify that `valkey.url` requires `redis://`, `rediss://`, or absolute three-slash `unix:///` (rejecting `valkey://`, `FACT-41`), and explain `GET /ready` returning HTTP `200` `degraded` when Valkey is down (completed jobs stay readable from SQLite, but new submissions and live queue queries fail; note both `examples/lyra-deployment/compose.yml:25` and `docker/docker-compose.yml:26` healthchecks pass on HTTP `200`, so check `status == "ready"` / `valkey.status == "ok"`, `FACT-41`).
  - Put one crisp **2-row revision table** (*"What revision do I bump?"*: `code_revision` vs `origin`/provider identity; `build_revision` was removed in `0cf17003`) in one place and link to it.

---

## 6. Understand Lyra (`concepts/*`)

- `[ ] UX-19` **Reorder for progressive mental models (and scope Valkey to server-only sections):** Move big-picture architecture (`model` + `architecture`) to position #1, then `graphs` (with merged `selection`), `lifecycle`, `caching-and-revisions` (`identity` + `caching`), and `provenance` last. Keep Page 1 (*How Lyra works*) centered on metrics, providers, dependency graphs, and local vs server execution; mention Valkey only briefly in the *Server architecture* subsection and in *Jobs & lifecycle* (explaining active queueing vs durable completed history). Keep full Valkey operational details in `operate/*`.
- `[ ] UX-20` **Introduce the running example briefly:** Don't assume readers of `concepts/*` have already finished the city tutorial before mentioning `city_street_maintenance`, `city_block_maintenance`, and `city_streets_for_blocks`.
- `[ ] UX-21` **Ground `concepts/provenance.md` in a concrete example:** Open with one small, real `ExecutionReport` JSON/Python snippet before walking through field tables, and explain *why* `ExecutionReport` exists alongside `runner.inspect()`.
- `[ ] UX-22` **Add simple visuals to `concepts/`:** Add clear Mermaid or SVG diagrams for (a) overall architecture (local in-process vs server API/queue/worker/SQLite), (b) dependency graph + canonical routing, and (c) `full` vs `per_location` cache partitioning.
- `[ ] UX-23` **Show *what breaks* when revisions aren't bumped** in `concepts/caching-and-revisions.md` (concrete stale-cache example) instead of abstract identity rules.
