---
meta:
  contentType: How-to
---

<!-- Content plan
Overview: Run local benchmarks, compare commits, and inspect CI reports.
Goal: Measure and investigate changes in Lyra's performance.
Audience: Contributors working on Lyra's implementation.
Content: Prepare the environment; choose a profile; save and compare results;
interpret measurements; inspect CI reports; maintain comparable workloads.
Open questions: None.
-->

# Run and compare performance benchmarks

Use [Airspeed Velocity (ASV)](https://asv.readthedocs.io/en/stable/) to measure Lyra’s execution time, peak memory, and output sizes. This guide helps you run benchmarks locally, compare commits, and inspect continuous integration (CI) reports.

## Prepare your environment

Run these commands from the repository root to install locked dependencies and register your machine with ASV:

```sh
uv sync --frozen
uv run asv machine --yes
```

Run measurements on an idle machine. Keep Python, dependencies, hardware, and thread settings consistent across comparisons.

## Choose a benchmark profile

Use the focused profile to measure seven representative operations during development:

```sh
benchmark_filter=$(uv run python -c \
  'from benchmarks.profiles import FAST_BENCH; print(FAST_BENCH)')
uv run asv run -E existing --dry-run --durations all \
  --bench "$benchmark_filter"
```

Use the full profile to measure all cases at a runnable checkpoint:

```sh
uv run asv run -E existing --dry-run --durations all
```

Both commands measure your current environment without saving results. The duration report includes fixture setup, which prepares each benchmark’s inputs and state.

Budget about one minute for the focused profile. Local baseline `d6576a8` took 7.5 minutes for the full suite and 4.1 minutes for the storage profile. These durations include discovery and fixture setup. Dependency installation takes additional time, and runtime varies by machine.

To measure point-location construction, add `--bench 'core.LocationsSuite.*construct'`. Use other `--bench` selectors to investigate individual subsystems.

To check that benchmarks execute, add `--quick --dry-run`. Use normal runs for performance comparisons so ASV calibrates and repeats measurements.

## Save and compare measurements

Save results from a clean checkout so each measurement matches its recorded commit. Use `--dry-run` for uncommitted changes, and run `uv sync --frozen` after checking out another commit.

At each commit you want to compare, save measurements and raw samples:

```sh
uv run asv run -E existing \
  --set-commit-hash "$(git rev-parse HEAD)" --record-samples
```

Repeat runs to estimate variability. To combine samples for the same commit, replace `--record-samples` with `--append-samples` on subsequent runs.

Compare two saved runs from the same machine. Replace `base_commit_sha` and `changed_commit_sha` with their commit hashes:

```sh
uv run asv compare base_commit_sha changed_commit_sha
```

To browse the saved history, generate and preview a dashboard:

```sh
uv run asv publish
uv run asv preview
```

ASV stores results and reports under `.asv/`, grouped by machine. Local and CI runs have separate histories.

## Interpret the measurements

Use the full suite to inspect costs across these areas:

| Area | Coverage |
| --- | --- |
| Catalogs | Graph validation, catalog compilation, local-runner initialization, and discovery; catalogs span 10–1,000 nodes |
| Preparation | Input validation, rejection, and computation namespace derivation, which identifies graph contracts for caching |
| Execution | Chains, shared dependencies, batch location acquisition, explicit location operations, and worker execution |
| Caching | Empty and populated filesystem caches, revision invalidation, and first versus repeated content fingerprint calculations |
| Data | Point-location construction/export, mixed nullable tables, polygon data, artifact encoding/decoding, and result serialization |
| Interfaces | Representational State Transfer (REST) routes, Model Context Protocol (MCP) tools, and retained result previews |
| Memory and size | Whole-process peak memory, encoded artifact bytes, and preview bytes |

The point-location and scalar-codec cases use 10,000 and 100,000 rows. Storage scenarios range from 16 to 10,000 rows; graph execution includes one-row and 1,000-row inputs. Each scenario selects representative parameters without testing every possible combination.

Timing excludes fixture preparation and includes production validation within the measured operation. Peak memory includes imported libraries, fixtures, retained inputs, and execution. Compare memory results with those setup costs in mind.

Fixtures use deterministic data and repeatable handlers. The suite substitutes in-process location providers and queue services, so it needs no live services or credentials. It exercises production execution, resource lifecycles, serialization, and filesystem caching.

REST measurements use an in-process Asynchronous Server Gateway Interface (ASGI) transport. MCP measurements cover tool dispatch and output serialization. Both exclude network traffic, client initialization, and event-loop initialization from timing. MCP measurements also exclude protocol session setup.

Interpret cache and fingerprint cases according to their prepared state:

- **Empty or invalidated cache**: reset temporary storage before each sample, execute once, and disable warmup
- **Populated cache**: populate entries before timing
- **First fingerprint**: compute a content identity from a fresh snapshot for each sample
- **Cached fingerprint or encoding**: reuse previously computed immutable identities

An empty application cache can still benefit from operating-system caches. These benchmarks measure application costs; deployment throughput also depends on external services and concurrent traffic.

## View CI reports

The [Benchmarks workflow](../.github/workflows/benchmarks.yml) runs on pushes to `main` and supports manual runs on a selected repository branch. Performance differences do not block merges or releases. Execution and report-publication failures remain visible in the workflow.

Download and extract the `performance-…` artifact from a workflow run. Replace `report_html_directory` with the extracted directory containing `index.html`, then serve the report:

```sh
uv run python -m http.server --directory report_html_directory
```

Open the [local performance report](http://localhost:8000) in your browser. The artifact includes the dashboard, raw results, and environment metadata:

- Python and ASV versions
- Installed packages and the dependency lockfile hash
- Thread settings
- Source commit and machine information

Artifacts expire after 30 days; the `benchmark-results` branch retains measurement history. Confirm differences with repeated runs on the same hardware because hosted runners can vary even with the same CPU model.

## Maintain comparisons as the application changes

Capture a baseline from a clean commit before substantial implementation work. Measure runnable checkpoints and compare them with both the baseline and the previous comparable checkpoint. Use the [capability ledger’s performance guidance](../capability-layer-plan/decision-ledger.md#performance-validation-during-implementation) to choose checkpoints that fit your implementation sequence.

Run relevant subsets when investigating a change; a full run after every edit is unnecessary. Continue using the suite on `main` and through manual runs after the feature is complete.

When adapting benchmark calls to changed APIs, preserve each workload’s inputs, expected behavior, timed boundaries, and cache state. New cases declare explicit workload versions; increase the version when those properties change, including through helper edits. The core benchmarks use ASV’s automatic source versions.

Record unavailable measurements as gaps, and start a new history for newly implemented behavior. Saved results let you compare implementations without maintaining an old implementation alongside the current one.

Investigate differences using absolute costs, ratios, and raw samples from repeated runs on idle hardware. Profile affected cases after confirming a difference, and identify dependency changes separately. Record performance tradeoffs and comparison gaps without imposing a universal regression threshold.

## Compare storage performance

Use the storage profile to measure data conversion and persistence through local and worker execution. Run it without saving results:

```sh
storage_filter=$(uv run python -c \
  'from benchmarks.profiles import STORAGE_BENCH; print(STORAGE_BENCH)')
uv run asv run -E existing --bench "$storage_filter" --dry-run --durations all
```

The profile includes scalar tables and polygons generated by buffering points. It also measures the following repeatable scenarios:

| Scenario | Rows | Contents |
| --- | ---: | --- |
| Typed scalars | 10,000 | Strings, integers, booleans, ordered categories, Coordinated Universal Time (UTC) timestamps, and floats, including null values |
| Varied points | 10,000 | Distinct 2D coordinates |
| Polygons with holes | 1,000 | 64 exterior vertices and a 16-vertex hole |
| Multipolygons | 1,000 | Three components, each with 32 exterior vertices and an 8-vertex hole |
| Complex polygons, small batch | 16 | 1,024 exterior vertices and a 64-vertex hole |
| Complex polygons, large batch | 128 | 1,024 exterior vertices and a 64-vertex hole |
| Mixed geometries | 1,200 | Six geometry types, with equal 2D and 3D representation |

Vertex counts exclude the closing coordinate, which repeats the first vertex. Spatial scenarios share the coordinate reference system `EPSG:4326` and the scalar control’s property columns.

Timing excludes fixture preparation. Interpret the measured operations as follows:

- **Export**: creates a complete, independent DataFrame or GeoDataFrame
- **Round trip**: constructs a fresh snapshot, encodes it, decodes it, and exports a frame, including the first fingerprint calculation
- **Encoding**: uses a snapshot with a cached fingerprint
- **Memory**: measures the process’s peak memory for typed scalars, points, and complex polygons, including imports and fixture preparation

To save a baseline and repeat the storage measurements, follow the [measurement commands](#save-and-compare-measurements) in this order:

1. Capture the full suite from a clean commit with `--record-samples`.
2. Archive the first pass, then repeat the storage profile with `--append-samples`.
3. Save the commands, environment metadata, raw samples, and benchmark definitions under `.asv/baselines/baseline_commit_sha/`. Replace `baseline_commit_sha` with the measured commit’s hash.

Use the duration report to budget subsequent comparisons. Keep the same environment and workload definitions when comparing results.
