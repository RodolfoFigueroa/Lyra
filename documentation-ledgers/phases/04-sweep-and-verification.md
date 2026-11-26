# Phase 4: Cross-Corpus Sweep, Rendered-Output Audit, & Final Verification

**Goal:** Perform a final mechanical quality sweep across all 49 authored pages, inspect generated and rendered site outputs, run full CI and escalated Python test verification, and close out all audit ledgers.

**Covered ledger IDs:** Final verification of `STYLE-07` through `STYLE-14`, vocabulary standard (`05-style-jargon-and-formatting.md`), and end-to-end validation of every `TOOL-*`, `FACT-*`, `IA-*`, and `UX-*` item.

---

## 1. Mechanical Cross-Corpus Sweep (`STYLE-07`–`STYLE-14`)

Run targeted read-only audits restricted to `docs/src/content/docs/` (never grep `docs/node_modules/` or `docs/dist/`):

- [ ] **US English (`STYLE-07`):**
  ```sh
  grep -rnE '\b(metres?|kilometres?|centreline|cancelling|behaviour|catalogue|normalise|serialise)\b' docs/src/content/docs/
  ```
  Expect **0** hits in authored pages (ignore Pint/external identifiers only if genuinely required).
- [ ] **Banned / retired jargon & Valkey leakage (Ledger 05 vocabulary standard):**
  ```sh
  grep -rnE '\b(retained|live definitions?|metric famil|descriptor|collection metadata|build_revision|deployment_changed|LYRA_DATA_DIR|calculate_feature_areas_m2)\b' docs/src/content/docs/ skills/lyra-metric/
  grep -rnE '\bValkey\b' docs/src/content/docs/index.md docs/src/content/docs/start/ docs/src/content/docs/quickstart.mdx docs/src/content/docs/use/
  ```
  Verify `Valkey` does not appear in Start Here or `use/*` (at most one brief link to `operate/` on `index.md` if needed), and zero retired terms remain.
- [ ] **Sidebar casing & frontmatter cleanliness (`STYLE-08`, `TOOL-10`):**
  ```sh
  grep -rnE '^(contentPlan|contentType):' docs/src/content/docs/
  grep -rn 'sidebar:' docs/src/content/docs/
  ```
  Confirm zero `contentPlan:`/`contentType:` fields and consistent **sentence case matching H1s** across `docs/navigation.json`, every authored page’s `sidebar.label`, and all 4 generated reference pages.
- [ ] **Typography, units, statuses, & GitHub URLs (`STYLE-09`, `STYLE-10`):**
  ```sh
  grep -rn '&nbsp;' docs/src/content/docs/
  grep -rnE '65,536|64 KB' docs/src/content/docs/
  grep -rnE 'HTTP `[0-9]{3}`' docs/src/content/docs/
  grep -rn 'github.com/RodolfoFigueroa/lyra' docs/src/content/docs/
  grep -rn '<span id=' docs/src/content/docs/
  ```
  Confirm `64 KiB` everywhere, plain `HTTP 404` style, uppercase `/RodolfoFigueroa/Lyra`, zero `&nbsp;` / `\u00a0`, zero `<span id=`, no straight apostrophes in prose, and no hard-wrapped prose paragraphs.
- [ ] **Code blocks, decorators, & highlight ranges (`STYLE-11`–`STYLE-14`):**
  - Confirm zero `.mdx` files lack JSX components (all tutorial chapters use `.mdx` + `<Steps>` + `<CodeExample>`; JSX-free guides use `.md`; `operate/troubleshooting.md` is `.md`).
  - Confirm zero split decorators (`"Append the handler immediately after its decorator"` returns 0 matches).
  - Spot-check every `{...}` line-highlight annotation across `docs/src/content/docs/` (both fenced blocks and `<CodeExample highlight=...>` props) to confirm none are out of bounds, off-by-one, or highlight entire blocks.
  - Confirm uniform `lyra-admin` invocation prefixes in `operate/` and keyword `LocationDependency(operation="...")` everywhere.

---

## 2. Rendered-Output & Artifact Spot Checks

After building the site (`cd docs && npm run ci`), inspect the generated outputs directly:

- [ ] **Generated MCP reference (`TOOL-01`, `TOOL-02`):**
  - Check line count of `docs/src/content/docs/reference/generated/mcp.md` (target ~350 lines, down from 4,214) and size reduction of `docs/public/llms-full.txt` (~90 KB+ smaller).
  - Verify `docs/dist/reference/generated/mcp/index.html` contains zero unintentional `<pre data-language="plaintext">` blocks inside tool descriptions and renders unwrapped output field tables for `lyra_get_job_result`, `lyra_describe_result`, and `lyra_inspect_report`.
- [ ] **Generated Config, HTTP, & CLI references (`TOOL-05`, `TOOL-06`, `TOOL-07`):**
  - Verify `docs/dist/reference/generated/configuration/index.html` contains zero literal `\|`, displays `const=8` on `schema_version`, and renders clean per-section tables.
  - Verify `docs/dist/reference/generated/http/index.html` shows `application/vnd.apache.arrow.stream`, real error status codes, and split `200`/`202` models on `/jobs/{job_id}/result/inspection`.
  - Verify `docs/dist/reference/generated/cli/index.html` includes `python -m lyra.server.main`, uses real command headings, and has zero `"Inspect help for ..."` fallbacks.
- [ ] **Scoped Python API & autolinks (`TOOL-03`, `TOOL-04`):**
  - Inspect `docs/dist/api/lyra/symbols.json`: confirm internal modules (`runtime.sqlite_*`, `server.routes.*`, `contracts.strict`, retired `utils.geometry`, etc.) are absent, while `LyraClient` / `AsyncLyraClient` and all public resource methods (`describe`, `submit`, `run`, `table`, `tables`, `export_geoparquet`, etc.) are present.
  - Confirm `docs/dist/api/client-exports/` does not exist.
  - Spot-check `docs/dist/reference/target-selection/index.html`, `concepts/caching-and-revisions/index.html`, `concepts/provenance/index.html`, and `reference/parameters/index.html` to verify `execute()`, `per_location`, `executed`, `parquet`, `context`, and `location_id` are **not** linked to internal symbols.
- [ ] **Mermaid diagrams (`UX-10`, `UX-22`):**
  - Inspect built HTML for all 5 diagram pages (`concepts/how-lyra-works`, `concepts/graphs`, `concepts/caching-and-revisions`, `plugins/tutorial/dependencies`, `plugins/tutorial/blocks`) to confirm Mermaid blocks render cleanly with no console/build errors and appear intact as fenced ` ```mermaid ` blocks in `docs/public/llms-full.txt`.

---

## 3. Full Verification Suite

Run both documentation CI and the full Python test suite (using sandbox escalation for the full pytest suite per `AGENTS.md`, since Phase 1 modifies `src/lyra/`):

- [ ] **Documentation CI pipeline:**
  ```sh
  cd docs && npm run ci
  ```
  Runs: `node --test scripts/*.test.mjs`, `scripts.generate_docs`, `astro build`, `astro check --noSync`, and `scripts.check_site --site dist` (broken links + anchors + API symbol verification).
- [ ] **Full Python test suite (with sandbox escalation):**
  ```sh
  uv run pytest -n auto --cov=lyra --cov-report=term-missing --cov-report=xml
  ```
- [ ] **Linter / type-checker checks (if configured in `pyproject.toml`):** Run project checks (e.g., `uv run ruff check` / type checks) on all touched Python files in `src/`, `docs/scripts/`, `examples/`, and `tests/`.

---

## 4. Ledger Closeout

- [ ] Verify every checklist item across `documentation-ledgers/01-*.md` through `05-*.md`, `documentation-ledgers/README.md`, and `documentation-ledgers/phases/01-*.md` through `04-*.md` is marked `[x]`.
- [ ] Ask the user whether to delete `documentation-ledgers/` in a final cleanup commit (matching prior commit `cc54db14`) or retain it in the repository.
