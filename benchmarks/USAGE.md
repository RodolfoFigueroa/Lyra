# Run and compare performance benchmarks

Use [Airspeed Velocity (ASV)](https://asv.readthedocs.io/en/stable/) to measure Lyra’s execution time and peak memory. Run benchmarks locally, compare saved commits, or download reports from continuous integration (CI).

## Run benchmarks locally

Run these commands from the repository root to synchronize dependencies, register your machine, and measure the current workspace without saving results:

```sh
uv sync --frozen
uv run asv machine --yes
uv run asv run -E existing --dry-run
```

Add `--bench construct` to measure location construction only. Add `--quick` to check that benchmarks execute; use normal runs for performance measurements because ASV calibrates and repeats them.

The suite measures four operations at 10,000 and 100,000 rows:

- Construct point locations from a GeoDataFrame
- Export point locations to a GeoDataFrame
- Encode scalar tables, reusing their cached content fingerprints
- Decode scalar tables

Timing excludes input preparation. Peak memory includes the entire process: imported libraries, input preparation, and input data. Each operation also includes any validation its production code performs.

## Save and compare measurements

Save measurements from a clean checkout so each result corresponds to its recorded commit. Synchronize dependencies with `uv sync --frozen` after checking out another commit, and use `--dry-run` for uncommitted changes.

Run this command at each commit you want to compare:

```sh
uv run asv run -E existing \
  --set-commit-hash "$(git rev-parse HEAD)" --record-samples
```

Compare two saved runs from the same machine. Replace `base_commit_sha` and `changed_commit_sha` with their commit hashes:

```sh
uv run asv compare base_commit_sha changed_commit_sha
```

Generate and preview a dashboard of your saved results:

```sh
uv run asv publish
uv run asv preview
```

ASV stores local results and reports under `.asv/`, grouped by machine. Local runs and CI runs have separate histories.

## View CI reports

The [Benchmarks workflow](../.github/workflows/benchmarks.yml) runs on pushes to `main`; you can also trigger it manually on `main`. Performance changes never block merges or releases, while execution and publication failures remain visible in the workflow.

Download and extract the `performance-…` artifact from a workflow run. Replace `path_to_html` with the extracted directory containing `index.html`, then serve the report locally:

```sh
uv run python -m http.server --directory path_to_html
```

Open `http://localhost:8000` in your browser. Downloaded artifacts include raw results and environment metadata alongside the dashboard.

Artifacts expire after 30 days, but the `benchmark-results` branch retains measurement history. Confirm small changes with repeated runs on the same hardware because hosted runner measurements vary.
