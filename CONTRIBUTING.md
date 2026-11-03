# Contributing to Lyra

This repository is a uv workspace containing the FastAPI application and three public Python packages. Update the public documentation in `docs/` whenever you change the behavior it describes.

## Repository map

Use this map to find the code or documentation you need to change:

| Path | Responsibility |
| --- | --- |
| `lyra_app/` | HTTP routes, configuration, plugin catalog, job store, workers, MCP, and deployment runtime. |
| `packages/lyra_sdk/` | Public plugin, catalog, geometry, job, and runtime contracts. |
| `packages/lyra_api/` | HTTP clients and the administrative CLI. |
| `packages/lyra_utils/` | Optional geospatial, date, and Earth Engine helpers. |
| `examples/city-plugin/` | Standalone teaching plugin, bundled data, runnable checkpoints, and isolated tests. |
| `examples/lyra-plugin/` | Smoke and integration test fixture. |
| `skills/lyra-metric/` | Portable agent instructions and bundled references for adapting workflows into metrics. |
| `tests/` | Unit, contract, route, client, worker, and documentation tests. |
| `docs/` | Astro Starlight site and generated-reference tooling. |

Start route changes in `lyra_app/routes/`, manifest changes in `packages/lyra_sdk/src/lyra/sdk/`, worker behavior in `lyra_app/worker.py`, and client behavior in `packages/lyra_api/src/lyra/api/client/`.

## Prepare your environment

Use uv for Python environment and package management:

```bash
uv sync
```

Install the selected plugins and configure each process before starting it:

- Give the API and workers the same `/lyra_data/config/lyra.toml`
- Supply both API keys to the API
- Supply resource credentials through files or environment variables on workers that need them

Start the API and workers independently:

```bash
uv run python -m lyra_app.main
uv run python -m lyra_app.worker_launcher interactive
```

The development Compose stack is the preferred integration environment:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

For editable plugin development, dependency changes, and multiple plugin checkouts, see [the deployment example](examples/lyra-deployment/README.md). Source edits need a manual process restart; contract edits also need manifest regeneration.

## Required verification

After changing Python, run all of these and fix failures caused by the change:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest \
  --cov=lyra_app \
  --cov=lyra \
  --cov-report=term-missing \
  --cov-report=xml
```

For a focused iteration, run the affected test file first, then run the full suite before handoff. Do not weaken lint, type, or coverage rules to make a change pass.

Validate documentation with:

```bash
npm ci --prefix docs
npm run generate --prefix docs
npm run check --prefix docs
npm run build --prefix docs
```

Generated reference files are ignored. Change source types, docstrings, configuration metadata, parsers, MCP contracts, or authored guides instead of editing generated Markdown or JSON.

Keep the six documentation areas focused on starting, running metrics, building plugins, operating deployments, understanding concepts, and looking up contracts. The city tutorial owns the progressive learning path; task guides remain independently usable. Maintain named examples and expected values in the standalone city project, and keep service clients isolated in tests.

Each guide declares its `contentType` and `contentPlan` in frontmatter. Keep its goal, audience, and section outline current in `docs/content-plan.json`; sidebar labels can use title case independently of sentence case page headings. When splitting a runnable Python example across snippets, give every piece the same filename title and update the documentation contract tests to execute them in order. `CodeExample` accepts a `title` for source-backed pieces. Nested groups in `docs/navigation.json` drive both the sidebar and portable exports.

## Change discipline

- Keep API catalog behavior separate from worker code execution. API processes read installed manifests; workers import trusted plugin code.
- Update SDK models, routes, clients, tests, and docs together when a public contract changes.
- Keep one authoritative example or explanation and link to it elsewhere.
- When SDK authoring contracts change, review `skills/lyra-metric/`, its bundled references, compatibility helper and tests, documented contract revision, and agent-assisted authoring guide together.
- When unit vocabulary changes, review the SDK enum, authoring unit tables, generated schemas, and skill compatibility checks together. Keep essential guidance usable outside this checkout.
- Rerun the [skill evaluations](tests/fixtures/skill_workflows/scenarios/README.md) for calculation preservation, plugin extension, missing information, contract conflicts, and file outputs. Check that agents ask about ambiguous cases before implementing dependent behavior. Behavioral trials provide evidence of compliance, not a mechanical guarantee.
- Use Conventional Commit titles. Release Please versions the application, SDK, API client (including the CLI), and utilities independently.
- Documentation deploys on pushes to `main`, product releases, and manual runs. Latest (`main`) lives at `/Lyra/latest/` and may include unreleased changes. Product release tags (`lyra-vX.Y.Z`) define snapshots at `/Lyra/versions/<version>/`; the site root opens the newest release. Historical `lyra-app-vX.Y.Z` tags remain valid documentation sources. Package-only tags do not publish a new documentation site.

## Releases

Merges to `main` feed Release Please. It maintains one aggregate release PR, while each package keeps its own version. Ordinary contributors only need to use a Conventional Commit PR title; they do not create tags or edit release metadata by hand.

For maintainers, the release workflow is:

1. Review the bot’s aggregate release PR. Confirm the proposed product and package versions, changelog entries, lockfile, and required CI checks.
2. Keep the PR open while more changes accumulate, or merge it when the product is ready to ship. The product version always advances when any package ships.
3. After merge, automation rebuilds and smoke-tests every distribution from the immutable merge commit. If validation succeeds, it creates tags only for changed packages, publishes the versioned and `latest` container images, then creates one GitHub Release named `Lyra vX.Y.Z` with a machine-readable component manifest attached.
4. The published product release triggers documentation validation and deployment.

CI checks the committed lockfile; the release-lock bot commits any required refresh to the release PR. Python checks verify SDK generation and publish coverage artifacts. Release publication requires Python, packaging, and docs checks. Documentation publishing requires only docs checks and reuses cached release builds; cache misses rebuild from tags. Only main participates in CI/CD.

The release publisher is intentionally ordered so no tag, image, or GitHub Release is created before validation passes. Its tag and release operations are idempotent: a failed run can be retried from GitHub Actions. If an existing tag points at a different commit, the run stops for manual investigation instead of moving the tag. Do not manually merge a second release PR or create release tags while a publication run is active.

If Release Please reports `There are untagged, merged release PRs outstanding`, check merged PRs carrying `autorelease: pending` and their publication runs. Retry a failed publisher before starting another release. If a maintainer chooses to supersede an unpublished release instead, remove its pending label and rerun Release Please; do not mark it `autorelease: tagged` or invent release tags. The configured `bootstrap-sha` is the last published release before the skipped v0.19.0 release. It bounds history scanning when a manifest version has no tag; normal tag-based discovery resumes once the replacement release is published.

The repository secret `RELEASE_PLEASE_TOKEN` must be able to update release PRs, push tags, create releases, and edit PR labels. GitHub’s package token publishes the container image. Component distributions are currently build artifacts and tags only; this pipeline does not publish them to PyPI.

Use `fix` for patch changes, `feat` for minor changes, and `!` or a `BREAKING CHANGE` footer for breaking changes. Below 1.0, breaking changes advance the minor version.

Main pushes validate each commit once. Release publication consumes those same checks and the validated Latest artifact, then deploys documentation after the release completes. Ordinary documentation deployments depend only on docs checks. PR checks remain separate. Rerun failed jobs to resume a publication.

Archived documentation uses the assembler’s `ARCHIVE_RECIPE_VERSION`. Bump it when changing historical rendering, fixing archive builds, or intentionally rebuilding archives with a changed toolchain. Routine dependency updates do not invalidate archives. Missing caches rebuild from release tags.

### API links in documentation

Backticked Python names in documentation prose automatically link to their API headings when the name identifies a unique destination. Qualified paths and unique suffixes such as `LocalRunner.run` are supported, including empty call parentheses. Use a fully qualified name or an explicit Markdown link to resolve ambiguity. Use a raw `<code>` element to keep a name unlinked. Headings and fenced examples do not receive automatic links.
