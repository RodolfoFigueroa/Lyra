# Lyra Documentation Overhaul — Phase Execution Ledgers

Actionable, self-contained execution playbooks derived from the five audit ledgers in [`documentation-ledgers/`](../README.md) (`01`–`05`). Each phase ledger groups changes into reviewable, CI-green batches with exact file paths, stable finding IDs, test updates, and verification gates.

**Context:** Lyra is in closed alpha with no existing deployments or external consumers. Backwards compatibility, legacy URL fragment anchors, and historical migration prose are unnecessary.

---

## Phase Map & Execution Order

Execute phases in order so tooling bugs and dead scaffolding are cleared before moving files, and the page tree is locked before rewriting prose.

| Phase | Playbook | Primary scope | Batches | Dependencies |
| --- | --- | --- | --- | --- |
| **Phase 1** | [01-tooling-and-contracts.md](./01-tooling-and-contracts.md) | `src/lyra/`, `examples/lyra-deployment/`, `docs/scripts/`, `docs/astro.config.mjs`, `docs/public/`, dead scaffolding | **1A** (`src/` + Docker/Compose) → **1B** (docs tooling + scaffolding) | Clean git working tree |
| **Phase 2** | [02-information-architecture.md](./02-information-architecture.md) | Structural deletes, merges, moves (`66 → 49` authored pages), `docs/navigation.json`, `README.md`, snippet test paths | Single structural batch (or section-group lockstep) | Phase 1 complete |
| **Phase 3** | [03-section-rewrites.md](./03-section-rewrites.md) | Section-by-section factual fixes (`FACT-*`), onboarding rewrites (`UX-*`), Mermaid diagrams, and style polish (`STYLE-*`) | **3A** Start & Concepts · **3B** Run Metrics · **3C** City Tutorial · **3D** Plugin Guides & Skill · **3E** Operate & Reference | Phase 2 complete *(3A, 3B, 3D, 3E can run in parallel once `navigation.json` is locked; coordinate shared test edits)* |
| **Phase 4** | [04-sweep-and-verification.md](./04-sweep-and-verification.md) | Mechanical style/jargon sweep, rendered-artifact audit, full CI + escalated test suite, ledger closeout | Single verification pass | Phase 3 complete |

---

## Settled Design Decisions

All open design, code-vs-doc, IA, and style questions are locked in below. Follow these consistently across every phase without re-litigating them.

| # | Topic | Ledger IDs | Settled decision |
| --- | --- | --- | --- |
| 1 | Default log stream | `FACT-20` | Switch `src/lyra/server/logging_config.py` to `logging.StreamHandler(sys.stdout)` so runtime matches docs and standard container logging. |
| 2 | `LocationProvider.resolve()` unknown-ID contract | `FACT-14` | Keep `src/lyra/runtime/provider_acquisition.py` as-is; document the actual pattern (return matching rows so Lyra raises `LocationProviderContractError` with exact missing IDs) and show imports from `lyra.contracts.errors`. |
| 3 | Public Python client API visibility | `TOOL-03` | Make the client resource classes public in `src/lyra/api/client/sync.py` and `async_.py`, scope `starlight-pydocs` strictly to the public API surface, and remove the `api/client-exports/{sync,async}` workaround from `docs/astro.config.mjs`. |
| 4 | Legacy GeoJSON helpers | `TOOL-03`, `FACT-13` | Retire `src/lyra/utils/geometry.py`, `src/lyra/contracts/geometry.py`, and their dedicated tests completely; update `tests/test_metric_skill_earth_engine.py` to construct `GeoDataFrame`s directly; remove `calculate_feature_areas_m2` from `docs/` and `skills/lyra-metric/references/authoring.md`. |
| 5 | Compose `/ready` healthcheck & `lyra-admin` extra | `FACT-35`, `FACT-41`, `UX-18` | Update Compose healthchecks to require `status == "ready"` (failing on `degraded` Valkey outages), and add `lyra[client]` to `examples/lyra-deployment/pyproject.toml` so `docker compose exec api lyra-admin` is officially supported. |
| 6 | Docker/Compose stack reconciliation | `UX-16`, `FACT-25` | Retire `docker/docker-compose.yml` (and the empty `docker/` directory); make `examples/lyra-deployment/` the single canonical Compose template; migrate `tests/test_docker_filesystem_layout.py` to test `examples/lyra-deployment/compose.yml` and `Dockerfile`; drop unused `/lyra_data/artifacts` and add `libpq5` in `examples/lyra-deployment/Dockerfile`. Keep root `Dockerfile` (used by `publish-release.yml`), `.env.example`, and `config.example.toml` (fixing its `LYRA_DATA_DIR` comment and `valkey.url` hostname). |
| 7 | Borderline IA pages & frontmatter | `IA-11`, `IA-24`, `IA-30`, `TOOL-10` | Keep `use/jobs.md` standalone; keep `plugins/earth-engine.md` standalone (structured with H2s + a complete `@metric` snippet); fold `plugins/descriptions.md` into `plugins/authoring.md`; delete `docs/content-plan.json` and drop **both** `contentPlan` and `contentType` frontmatter. |
| 8 | City plugin tutorial cleanup | `UX-08`, `IA-19`, `IA-20` | Aggressive cleanup: single-module `city_example/tutorial.py` from Ch. 1, `if __name__ == "__main__":` guards, rebalance into **7 uniform `.mdx` chapters** with `<Steps>` and `<CodeExample>` (Ch. 1–4 core progression, Ch. 5 canonical block-to-street routing, Ch. 6 block-level summary, Ch. 7 test and package), and update `tests/tutorial_helpers.py` + `tests/test_docs_contract.py`. |
| 9 | Diagrams (Concepts & Tutorial) | `UX-10`, `UX-22`, `FACT-10`, `FACT-30` | Add a Mermaid build-time plugin to `docs/` and author **all** diagrams as fenced `mermaid` blocks—both the 3 new `concepts/` diagrams and replacements for the 2 broken SVG components (`BlockWorkloadDiagram.astro`, `MaintenanceDependenciesDiagram.astro`, which get deleted along with their `normalize_mdx()` strip regexes). |
| 10 | Reverse-proxy guidance | `UX-18` | Do **not** include an nginx/Caddy reverse-proxy config example; keep only a brief note on configuring `api.public_base_url`, `api.forwarded_allow_ips`, and matching the `Host` header for MCP when running behind a proxy. |
| 11 | House style & typography | `STYLE-07`–`STYLE-14` | **Sentence case matching page H1s** for sidebar labels (including generated pages); US English; `64 KiB` for payload byte limits; plain HTTP status codes (`HTTP 404`, not backticked); closed prefixes (`nonempty`, `nonblank`, `nonnegative`, `noncanonical`) with hyphenated `non-null`; `operate/troubleshooting.md` stays `.md`; **metric function** for `@metric`, **callback** for operation mappers/selectors; primary method spellings `client.results.tables()` and `store.describe()` (noting aliases once in reference). |
| 12 | Ledger lifecycle | — | Keep `documentation-ledgers/` checked off as batches complete; remove `documentation-ledgers/` only at the end of Phase 4 upon explicit user confirmation. |

---

## Cross-Phase Working Rules

1. **Follow `AGENTS.md` policies in every batch:**
   - **Behavioral testing only:** Keep and migrate behavioral assertions when moving executable doc snippets; never add tests that pin prose, wording, or documentation topics.
   - **Authoring guidance:** Describe only the current supported contract—no migration notes, retired-feature lists, or historical comparisons.
   - **No wheel smoke checks:** Use the existing `uv` and `npm` project environments.
   - **Test resource isolation:** Never contact live Valkey, Postgres, HTTP, or cloud services in tests.
   - **Sandbox escalation for full pytest:** Focused doc tests run sandboxed; run the full `uv run pytest -n auto --cov=lyra ...` suite with sandbox escalation because the restricted sandbox blocks asyncio socket wakeups.
2. **Keep CI green at every gate:** Never leave broken links, unresolvable `navigation.json` entries, or failing executable-snippet tests across phase boundaries.
3. **Update checkboxes in both places:** When a batch finishes, check off `[x]` in the active phase ledger (`phases/0*.md`) **and** the matching IDs in the parent audit ledgers (`../01-*.md`–`../05-*.md` and `../README.md`).
4. **Pre-Phase 1 housekeeping:** Commit or reconcile any uncommitted edits in `documentation-ledgers/` on the `documentation` branch before starting Batch 1A so every phase diff is clean and easy to review.
