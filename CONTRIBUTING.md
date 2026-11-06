# Contributing to Lyra

Use uv to work on the `lyra` Python distribution in this repository. Update the public documentation in `docs/` whenever you change the behavior it describes.

## Repository map

Use this map to find the code or documentation you need to change:

| Path | Responsibility |
| --- | --- |
| `src/lyra/server/` | HTTP routes, configuration, plugin catalog, job store, workers, MCP, and deployment runtime. |
| `src/lyra/contracts/` | Shared models for manifests, configuration, and wire formats. |
| `src/lyra/data/` | Native tables, geometry, and serialization. |
| `src/lyra/sdk/` | Plugin authoring and manifest generation. |
| `src/lyra/runtime/` | Catalog planning and metric execution, including local runs. |
| `src/lyra/api/` | HTTP clients and the administrative CLI. |
| `src/lyra/utils/` | Optional geospatial, date, and Earth Engine helpers. |
| `examples/city-plugin/` | Standalone teaching plugin, bundled data, runnable checkpoints, and isolated tests. |
| `examples/lyra-plugin/` | Smoke and integration test fixture. |
| `skills/lyra-metric/` | Portable agent instructions and bundled references for adapting workflows into metrics. |
| `tests/` | Unit, contract, route, client, worker, and documentation tests. |
| `docs/` | Astro Starlight site and generated-reference tooling. |

For server routes, start in `src/lyra/server/routes/`. For worker behavior, see `src/lyra/server/worker.py`. The HTTP client implementations live in `src/lyra/api/client/`.

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
uv run python -m lyra.server.main
uv run python -m lyra.server.worker_launcher interactive
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
  --cov=lyra \
  --cov-report=term-missing \
  --cov-report=xml
```

For a focused iteration, run the affected test file first, then run the full suite before handoff. Do not weaken lint, type, or coverage rules to make a change pass.

Validate documentation with:

```bash
npm ci --prefix docs
npm run ci --prefix docs
```

Generated reference files are ignored. Change source types, docstrings, configuration metadata, parsers, MCP contracts, or authored guides instead of editing generated Markdown or JSON.

Keep the six documentation areas focused on starting, running metrics, building plugins, operating deployments, understanding concepts, and looking up contracts. The city tutorial owns the progressive learning path; task guides remain independently usable. Maintain named examples and expected values in the standalone city project, and keep service clients isolated in tests.

Each guide declares its `contentType` and `contentPlan` in frontmatter. Keep its goal, audience, and section outline current in `docs/content-plan.json`; sidebar labels can use title case independently of sentence case page headings. When splitting a runnable Python example across snippets, give every piece the same filename title and update the documentation contract tests to execute them in order. `CodeExample` accepts a `title` for source-backed pieces. Nested groups in `docs/navigation.json` drive both the sidebar and portable exports.

## Change discipline

- Keep API catalog behavior separate from worker code execution. API processes read installed manifests; workers import trusted plugin code.
- Update shared contracts, routes, clients, tests, and docs together when a public contract changes.
- Keep one authoritative example or explanation and link to it elsewhere.
- When SDK authoring contracts change, review `skills/lyra-metric/`, its bundled references, compatibility helper and tests, documented contract revision, and agent-assisted authoring guide together.
- When unit vocabulary changes, review the `lyra.contracts.units.Unit` enum, authoring unit tables, generated schemas, and skill compatibility checks together. Keep essential guidance usable outside this checkout.
- Rerun the [skill evaluations](tests/fixtures/skill_workflows/scenarios/README.md) for calculation preservation, plugin extension, missing information, contract conflicts, and file outputs. Check that agents ask about ambiguous cases before implementing dependent behavior. Behavioral trials provide evidence of compliance, not a mechanical guarantee.
- Use Conventional Commit titles so Release Please can determine the next Lyra version.

## Publish a Lyra release

Release Please opens a release pull request (PR) for changes on `main`. Review the proposed version and its entry in the root `CHANGELOG.md`. The release-lock workflow updates the root and example lockfiles. Check that they resolve Lyra at the proposed version.

Example plugins have their own distribution names and release versions.

Leave the PR open to include more changes. When the required checks pass and you’re ready to publish, merge the PR. Wait for publication to finish before merging another release PR.

Continuous integration (CI) builds and smoke-tests the Python distribution at the merge commit. After the Python and documentation checks pass, the publisher pushes the container image with the version and `latest` tags. It then tags the same commit as `vX.Y.Z` and publishes the GitHub Release using the root changelog entry. The attached `release-manifest.json` records the version, tag, and merge commit.

The repository secret `RELEASE_PLEASE_TOKEN` must be able to update release PRs, push tags, create releases, and edit PR labels. GitHub’s package token publishes the container image. To install the Python distribution from a release, follow [Install Lyra for Python](docs/src/content/docs/start/installation.md).

Use `fix` for patch changes, `feat` for minor changes, and `!` or a `BREAKING CHANGE` footer for breaking changes. Below 1.0, breaking changes advance the minor version.

### Retry a failed release

In GitHub Actions, open the failed publication run and rerun the failed jobs. The publisher stops if the release tag points to a different commit. A retry can finish a draft release. If the release is already public, the publisher skips the image push and verifies the attached manifest.

If Release Please reports `There are untagged, merged release PRs outstanding`, find the merged PR labeled `autorelease: pending`. Check its publication run for failed jobs. After publication succeeds, the publisher replaces the pending label with `autorelease: tagged`.

## Documentation on GitHub Pages

Open `/Lyra/latest/` for changes on `main`. The site root also opens Latest until Lyra publishes its first stable `vX.Y.Z` release. After that, the root opens the highest published stable version at `/Lyra/versions/X.Y.Z/`.

Pushes to `main` and successful releases trigger the GitHub Pages workflow. You can also run it manually from GitHub Actions. Pages reuses the validated Latest build when it matches the current `main` commit. For release documentation, it builds from the commit the release tag points to.

Before deployment, Pages checks the `main` commit, release tag, and release commit again. If any of them changed during the build, Pages skips deployment.

### API links in documentation

Backticked Python names in documentation prose automatically link to their API headings when the name identifies a unique destination. Qualified paths and unique suffixes such as `LocalRunner.run` are supported, including empty call parentheses. Use a fully qualified name or an explicit Markdown link to resolve ambiguity. Use a raw `<code>` element to keep a name unlinked. Headings and fenced examples do not receive automatic links.
