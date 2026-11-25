# Lyra Documentation Overhaul Ledgers

Durable reference ledgers capturing the full read-only audit of `docs/` against `src/lyra/` and `examples/` (audited 2026-11-24; updated through commit `20c90a3bf10c00c9bb0b1b199baae8b5723b3a79`, which moved terminal job status and result storage from Redis to SQLite).

**Context:** Lyra is in closed alpha with no existing clients or deployments. Backwards compatibility, legacy URL fragment anchors, and historical migration notes are unnecessary.

## Ledger Index

| Ledger | Scope | Primary use |
| --- | --- | --- |
| [01-tooling-and-generated-docs.md](./01-tooling-and-generated-docs.md) | `docs/scripts/`, `astro.config.mjs`, `starlight-pydocs`, `remark-api-links.mjs`, `reference/generated/*`, `docs/public/*`, `docs/archive/`, `docs/content-plan.json` | Stage 1: fix broken builds/renderings and remove scaffolding |
| [02-factual-errors.md](./02-factual-errors.md) | Every verified factual error, broken command, wrong diagram, wrong exception, and code/doc drift across all sections | Stage 3: fix alongside each section's rewrite |
| [03-information-architecture.md](./03-information-architecture.md) | Page-by-page disposition of all 66 authored + 4 generated pages, duplication map, and target `navigation.json` (~66 → ~33 authored pages) | Stage 2: delete, merge, move, and deduplicate |
| [04-onboarding-and-section-rewrites.md](./04-onboarding-and-section-rewrites.md) | First-time user friction, pedagogy fixes, missing production/authoring essentials, and section rewrite briefs | Stage 3: rewrite newcomer journeys & core guides |
| [05-style-jargon-and-formatting.md](./05-style-jargon-and-formatting.md) | Terminology glossary, spec-sheet/AI-prose cleanup, US English fixes, highlight ranges, `.md`/`.mdx` & sidebar standards | Stages 3–4: apply during rewrites & final sweep |

## Root-Cause Summary

1. **Incremental accretion:** 66 authored pages + 4 generated pages + 169 auto-generated Python API pages for a closed-alpha library. Contains 3 competing synthetic cities, 10 operator pages (down to 42 lines), 17 standalone plugin guides alongside an 8-chapter tutorial, 1-sentence redirect H2 stubs, verbatim copy-pasted paragraphs, and 25 legacy `<span id="...">` anchor hacks.
2. **Internal spec / test-invariant voice:** User guides frequently read like translated unit-test invariants and defensive design specs rather than mental models or task guides.
3. **Over-engineered tooling & mechanical compliance:** `content-plan.json` (1,050 lines), mechanical `<=25`-line code splits separating `@metric`/`@location_operation` from `def`, `str.title()` sidebar labels, a 4,214-line generated MCP page embedding raw JSON Schemas (16.8% of the 599 KB `llms-full.txt`), and a `starlight-pydocs` setup that dumps 2,711 internal symbols while hiding public client methods and corrupting inline code autolinks.

## Master Execution Checklist

Recommended order minimizes rework by clearing tooling/scaffolding and locking the page layout before rewriting prose (combining factual fixes, onboarding improvements, and style cleanup in one pass per section).

### Stage 1 — Tooling, Generated Reference, and Dead Scaffolding
- [ ] Fix MCP tool docstrings & `generate_docs.py` (`cleandoc`, strip `Returns:`/`Raises:`, stop embedding raw JSON Schemas in `mcp.md`, unwrap union tables) (`TOOL-01`, `TOOL-02`)
- [ ] Fix `generate_docs.py` config & HTTP & CLI generators (merging claim, secret sources, `const: 6`, literal `\|`, Arrow content-type, missing HTTP error statuses, CLI headings) (`TOOL-05`, `TOOL-06`, `TOOL-07`)
- [ ] Scope `starlight-pydocs` to public API, expose public client resource classes, remove `api/client-exports` hack (`TOOL-03`)
- [ ] Restrict `remark-api-links.mjs` so ordinary tokens stop mis-linking to internals (`TOOL-04`)
- [ ] Regenerate and schema-validate `docs/public/examples/mcp/*.json` (`TOOL-08`)
- [ ] Delete `docs/archive/lyra-tui-changelog.md`, `docs/content-plan.json` (+ its test in `tests/test_docs_contract.py`), and all 25 `<span id="...">` anchor hacks (`TOOL-09`, `TOOL-10`, `TOOL-11`)
- [ ] Resolve two code-vs-doc decisions up front: `logging_config.py` `stdout` vs `stderr` (`FACT-20`) and `provider_acquisition.py` `UnresolvedLocationIDsError` wrapping (`FACT-14`)

### Stage 2 — Information Architecture Consolidation (~66 → ~33 authored pages)
- [ ] Execute page deletions, merges, and moves in [03-information-architecture.md](./03-information-architecture.md) and remove all 12 one-sentence redirect stubs
- [ ] Update `docs/navigation.json`, `plugins/index.md`, `reference/index.md`, `reference/python.md`, and `README.md` links
- [ ] Update `tests/test_docs_contract.py` and `tests/tutorial_helpers.py` for moved/renamed/merged pages

### Stage 3 — Section-by-Section Rewrite, Fact-Check, and De-Jargonization
Apply factual fixes ([02-factual-errors.md](./02-factual-errors.md)), onboarding fixes ([04-onboarding-and-section-rewrites.md](./04-onboarding-and-section-rewrites.md)), and house style ([05-style-jargon-and-formatting.md](./05-style-jargon-and-formatting.md)) together in one pass per section:
- [ ] **Start Here & Concepts** — lead with metrics, plugins, and local execution (keep Redis out of Start Here and consumer mental models); reorder `concepts/` big-picture first; resolve `FACT-01`–`FACT-03`, `FACT-36`
- [ ] **Run Metrics (`use/`)** — restructure `use/results.md` around output formats (not Redis/queue expiry) with happy path first; trim duplication in `python-client`, `rest-api`, `mcp`, `local-execution`, `spatial-inputs`, `jobs`; resolve `FACT-04`–`FACT-08`, `FACT-29`, `FACT-34`, `FACT-37`, `FACT-39`
- [ ] **Build Plugins (`plugins/tutorial/` → `plugins/`)** — rework city tutorial (6–7 chapters, single-module flow, sample outputs, `if __name__ == "__main__":`, redraw `BlockWorkloadDiagram.astro` & `MaintenanceDependenciesDiagram.astro`), then add real code examples & minimal `pyproject.toml` template to standalone guides; resolve `FACT-09`–`FACT-17`, `FACT-26`–`FACT-28`, `FACT-30`–`FACT-33`
- [ ] **Deploy & Operate (`operate/`)** — reconcile root `docker/` vs `examples/lyra-deployment/`, keep full Redis/SQLite/readiness operational details here, fill production gaps; resolve `FACT-18`–`FACT-21`, `FACT-23`–`FACT-25`, `FACT-35`, `FACT-38` (`FACT-22` already resolved in `20c90a3b`)
- [ ] **Handwritten Reference (`reference/`)** — tighten `python.md`, `parameters.md`, `target-selection.md`, `units.md`, `location-artifacts.md`, `mcp-payloads.md`; resolve `FACT-27`, `FACT-39`

### Stage 4 — Final Cross-Corpus Sweep & Verification
- [ ] Mechanical sweep for US English, sidebar casing, code highlight ranges, split decorators, straight apostrophes, and broken links
- [ ] Verify cleanly: `cd docs && npm run ci` and `uv run pytest tests/test_docs_contract.py tests/test_docs_generation.py tests/test_documented_parameters.py`

## How to Use These Ledgers Across Sessions

- Check off `[x]` items as PRs or sessions complete them.
- Each item carries a stable ID (`TOOL-##`, `FACT-##`, `IA-##`, `UX-##`, `STYLE-##`) so commits and follow-up sessions can reference exact items without re-auditing.
- When editing docs that have executable snippet tests, check `tests/test_docs_contract.py`, `tests/test_docs_generation.py`, `tests/test_documented_parameters.py`, and `tests/tutorial_helpers.py`.
