# Phase 2: Information Architecture Consolidation

**Goal:** Execute all structural page deletions, merges, renames, and redirect-stub removals—consolidating **66 authored pages → 49 authored pages** (17 pages deleted/merged, 11 redirect stubs deleted)—while keeping `docs/navigation.json`, internal links, root `README.md`, and executable-snippet tests green before doing deep prose rewrites in Phase 3.

**Covered ledger IDs:** `IA-01` through `IA-53` (structural moves/merges/deletions), `STYLE-08` (sentence-case sidebar labels in `navigation.json` & frontmatter), `STYLE-11` (`.md` vs `.mdx` extensions on renamed/converted files).

> **Why do Phase 2 before Phase 3?**
> Doing structural consolidation first preserves clean git rename history, stops anyone from rewriting pages destined for deletion, and satisfies two strict CI invariants in lockstep:
> 1. `test_navigation_is_complete_unique_and_resolvable` (`tests/test_docs_contract.py:152-173`) enforces an exact 1:1 match between `docs/navigation.json` and every `.md`/`.mdx` file under `docs/src/content/docs/` (outside `reference/generated/`).
> 2. `docs/scripts/check_site.py` fails the build on any broken internal page link or heading anchor.

---

## 1. Exact Page Dispositions (66 → 49 Authored Pages)

### Start Here (`5 → 4` pages)
- [ ] **Fold & delete** `start/choose-execution.md` → fold its concise local-vs-server comparison table into `index.md` (`IA-01`, `IA-02`).
- [ ] **Keep** `start/installation.md`, `start/local.mdx`, `quickstart.mdx` (`IA-03`–`IA-05`).

### Run Metrics (`8 → 7` pages)
- [ ] **Fold & delete** `use/location-exports.md` → move its concise GeoParquet export section into `use/results.md` (`IA-12`, `IA-13`).
- [ ] **Keep** `use/local-execution.md`, `use/python-client.md`, `use/rest-api.md`, `use/mcp.md`, `use/spatial-inputs.md`, `use/jobs.md` (standalone per settled decision), `use/results.md` (`IA-06`–`IA-11`).

### Build Plugins — Tutorial (`9 → 8` pages: `index` + 7 uniform `.mdx` chapters)
- [ ] **Keep & convert to `.mdx`** (`IA-14`–`IA-20`):
  - `plugins/tutorial/index.md`
  - `plugins/tutorial/first-metric.mdx` (Ch. 1)
  - `plugins/tutorial/select-streets.mdx` (Ch. 2)
  - `plugins/tutorial/parameters.mdx` (Ch. 3)
  - `plugins/tutorial/dependencies.mdx` (Ch. 4)
  - `plugins/tutorial/canonical.md` → **rename to** `plugins/tutorial/canonical.mdx` (moved before `blocks.mdx` as Ch. 5: canonical block-to-street routing)
  - `plugins/tutorial/blocks.mdx` (Ch. 6: membership + weighted block summary)
  - `plugins/tutorial/testing.md` + `plugins/tutorial/package.md` → **merge into** `plugins/tutorial/test-and-package.mdx` (Ch. 7) and delete both old `.md` files.
- *Note:* If rewriting the tutorial’s Python code blocks immediately in Phase 2 would bloat the diff, keep the existing executable blocks intact during the Phase 2 move (updating `tests/tutorial_helpers.py` chapter filenames only) and perform the full `city_example/tutorial.py` code refactor cleanly in **Phase 3, Batch 3C**.

### Build Plugins — Landing & Standalone Guides (`18 → 12` pages: `index` + 11 guides)
- [ ] **Merge & delete** `plugins/metrics.md` + `plugins/descriptions.md` → fold essential content into `plugins/authoring.md`; delete `plugins/metrics.md` and `plugins/descriptions.md` (`IA-22`, `IA-24`).
- [ ] **Convert extension** `plugins/dependencies.mdx` → `plugins/dependencies.md` (JSX-free) (`IA-25`, `STYLE-11`).
- [ ] **Merge & delete** `plugins/canonical-selection.md` → fold into `plugins/location-operations.md`; delete `plugins/canonical-selection.md` (`IA-26`).
- [ ] **Delete** `plugins/location-example.mdx` → carry over a concise inline `LocationProvider` / two-plugin example into `plugins/location-providers.md` (and migrate behavioral test coverage in `tests/test_docs_contract.py`) (`IA-27`, `IA-28`).
- [ ] **Merge & delete** `plugins/runtime.md` → fold `RunContext` (`logger`, `temp_dir`, `report_progress`) into `plugins/resources.md`; delete `plugins/runtime.md` (`IA-29`).
- [ ] **Merge, rename, & delete** `plugins/quickstart.mdx` + `plugins/publish-and-debug.mdx` → create `plugins/packaging.md`; delete `plugins/quickstart.mdx` and `plugins/publish-and-debug.mdx` (`IA-32`, `STYLE-11`).
- [ ] **Keep** `plugins/index.md`, `plugins/outputs.md`, `plugins/earth-engine.md` (standalone per settled decision), `plugins/testing.md`, `plugins/development.mdx`, `plugins/agent-assisted-authoring.mdx` (`IA-21`, `IA-23`, `IA-30`, `IA-31`, `IA-33`, `IA-34`).

### Deploy and Operate (`10 → 6` pages)
- [ ] **Fold & delete** `operate/requirements.md` → merge into a *"Before you start"* section at the top of `operate/deployment.mdx`; delete `operate/requirements.md` (`IA-35`, `IA-36`).
- [ ] **Fold & delete** `operate/resources.md` + `operate/caching.md` → merge valid TOML resource config (`resources.md:11-36`, dropping obsolete single-provider claim `L38-42`) and SQLite/storage operations (`caching.md`, dropping verbatim copy-paste and false pagination) into `operate/configuration.md`; delete `operate/resources.md` and `operate/caching.md` (`IA-37`).
- [ ] **Merge & delete** `operate/runbook.md` + `operate/admin-cli.md` → create `operate/monitoring.md`; delete `operate/runbook.md` and `operate/admin-cli.md` (`IA-39`).
- [ ] **Keep** `operate/plugins.mdx`, `operate/updates.mdx`, `operate/troubleshooting.md` (stays `.md`) (`IA-38`, `IA-40`, `IA-41`).

### Understand Lyra (`8 → 5` pages, reordered big-picture first)
- [ ] **Merge & rename** `concepts/model.md` + `concepts/architecture.md` → create `concepts/how-lyra-works.md` (position #1); delete both old files (`IA-42`).
- [ ] **Fold & delete** `concepts/selection.md` → merge into `concepts/graphs.md` (position #2); delete `concepts/selection.md` (`IA-43`).
- [ ] **Keep** `concepts/lifecycle.md` (position #3) (`IA-44`).
- [ ] **Merge & rename** `concepts/identity.md` + `concepts/caching.md` → create `concepts/caching-and-revisions.md` (position #4); delete both old files (`IA-45`).
- [ ] **Keep** `concepts/provenance.md` (position #5) (`IA-46`).

### Reference (`8 → 7` authored pages + 4 generated + scoped `api/lyra`)
- [ ] **Fold & delete** `reference/locations.md` (10-line stub) → merge into `reference/location-artifacts.md`; delete `reference/locations.md` (`IA-52`).
- [ ] **Keep** `reference/index.md`, `reference/python.md`, `reference/parameters.md`, `reference/target-selection.md`, `reference/units.md`, `reference/mcp-payloads.md` (`IA-47`–`IA-51`, `IA-53`).

---

## 2. Delete All 11 One-Sentence Redirect H2 Stubs

Remove every empty/1-sentence H2 section whose sole job is pointing to another page (update any incoming anchors to point directly to the canonical page):

- [ ] `operate/configuration.md` (formerly lines `28`, `82`)
- [ ] `operate/plugins.mdx` (formerly line `60`)
- [ ] `operate/runbook.md` → `operate/monitoring.md` (formerly line `87`)
- [ ] `plugins/runtime.md` (4 stubs, formerly lines `52-66` — removed when deleting `runtime.md`)
- [ ] `plugins/location-operations.md` (2 stubs, formerly lines `180-187`)
- [ ] `plugins/authoring.md` (formerly lines `118-120`)

---

## 3. Target `docs/navigation.json` (Sentence-Case Labels)

Update `docs/navigation.json` and matching frontmatter `sidebar.label` fields to **sentence case matching each page’s H1** (`STYLE-08`):

```json
[
  {
    "label": "Start here",
    "items": [
      "index",
      "start/installation",
      "start/local",
      "quickstart"
    ]
  },
  {
    "label": "Run metrics",
    "items": [
      "use/local-execution",
      "use/python-client",
      "use/rest-api",
      "use/mcp",
      "use/spatial-inputs",
      "use/jobs",
      "use/results"
    ]
  },
  {
    "label": "Build plugins",
    "items": [
      "plugins/index",
      {
        "label": "Build a city plugin",
        "items": [
          "plugins/tutorial/index",
          "plugins/tutorial/first-metric",
          "plugins/tutorial/select-streets",
          "plugins/tutorial/parameters",
          "plugins/tutorial/dependencies",
          "plugins/tutorial/canonical",
          "plugins/tutorial/blocks",
          "plugins/tutorial/test-and-package"
        ]
      },
      {
        "label": "Define calculations",
        "items": [
          "plugins/authoring",
          "plugins/outputs"
        ]
      },
      {
        "label": "Compose graphs",
        "items": [
          "plugins/dependencies",
          "plugins/location-operations"
        ]
      },
      {
        "label": "Access data",
        "items": [
          "plugins/location-providers",
          "plugins/resources",
          "plugins/earth-engine"
        ]
      },
      {
        "label": "Test and ship",
        "items": [
          "plugins/testing",
          "plugins/development",
          "plugins/packaging",
          "plugins/agent-assisted-authoring"
        ]
      }
    ]
  },
  {
    "label": "Deploy and operate",
    "items": [
      "operate/deployment",
      "operate/configuration",
      "operate/plugins",
      "operate/monitoring",
      "operate/updates",
      "operate/troubleshooting"
    ]
  },
  {
    "label": "Understand Lyra",
    "items": [
      "concepts/how-lyra-works",
      "concepts/graphs",
      "concepts/lifecycle",
      "concepts/caching-and-revisions",
      "concepts/provenance"
    ]
  },
  {
    "label": "Reference",
    "items": [
      "reference/index",
      "reference/python",
      "reference/parameters",
      "reference/target-selection",
      "reference/units",
      "reference/location-artifacts",
      "reference/mcp-payloads",
      "reference/generated",
      "api/lyra"
    ]
  }
]
```

*Note:* Also check `docs/astro.config.mjs:19` (`collapsed: item.label !== 'Start Here'`) and update the comparison string to `'Start here'` so the first sidebar group stays expanded by default!

---

## 4. Update Landing Pages, Cross-Links, & Test Targets

- [ ] **Landing pages:** Update `plugins/index.md` (`IA-21`, `FACT-33` — list all 11 standalone guides + tutorial accurately) and `reference/index.md` (`IA-47`).
- [ ] **Root `README.md`:** Update links that moved or were deleted (`operate/requirements/` → `operate/deployment/`, `operate/admin-cli/` → `operate/monitoring/`, `Use the REST API` → `Use the HTTP API`).
- [ ] **Cross-page links across `docs/src/content/docs/`:** Rewrite every link targeting the 17 deleted/merged slugs (`start/choose-execution`, `use/location-exports`, `plugins/metrics`, `plugins/descriptions`, `plugins/canonical-selection`, `plugins/location-example`, `plugins/runtime`, `plugins/quickstart`, `plugins/publish-and-debug`, `operate/requirements`, `operate/resources`, `operate/caching`, `operate/runbook`, `operate/admin-cli`, `concepts/model`, `concepts/architecture`, `concepts/selection`, `concepts/identity`, `concepts/caching`, `reference/locations`, `plugins/tutorial/testing`, `plugins/tutorial/package`).
- [ ] **Executable-snippet tests in `tests/test_docs_contract.py` & `tests/tutorial_helpers.py`:**
  - `plugins/dependencies.mdx` → `plugins/dependencies.md` (`test_docs_contract.py:617`).
  - `plugins/quickstart.mdx` (`test_plugin_quickstart_python_excerpts_are_executable`, `L752`): migrate to execute the canonical packaging/authoring example or remove if superseded by the tutorial + `authoring.md` coverage (preserving behavioral coverage of `LocalRunner.from_definitions`).
  - `plugins/location-example.mdx` (`test_published_location_graph`, `L1033`): migrate to exercise the surviving inline `LocationProvider` / two-plugin example in `plugins/location-providers.md` so provider lifecycle (`setup`/`resolve`/`close`) and missing-ID `LocationProviderContractError` coverage is preserved.
  - `tests/tutorial_helpers.py:57-66`: update `pages` tuple to match the new tutorial filenames (`canonical.mdx`, `test-and-package.mdx`) and keep `test_published_city_tutorial_builds_progressively` passing until Batch 3C’s full tutorial rewrite.

---

## Phase 2 Verification Gate

```sh
cd docs && npm run ci
uv run pytest -n auto tests/test_docs_contract.py tests/test_docs_generation.py tests/test_documented_parameters.py
```

### Acceptance Criteria
- [ ] Exactly **49 authored pages** exist under `docs/src/content/docs/` (outside `reference/generated/`), matching `docs/navigation.json` 1:1.
- [ ] Zero 1-sentence redirect H2 stubs remain anywhere in `docs/src/content/docs/`.
- [ ] `docs/scripts/check_site.py` reports **zero** broken internal links or missing anchors.
- [ ] `Start here` stays expanded in the built sidebar (`astro.config.mjs:19` updated).
- [ ] Every executable-snippet test in `tests/test_docs_contract.py` passes with behavioral coverage preserved.
