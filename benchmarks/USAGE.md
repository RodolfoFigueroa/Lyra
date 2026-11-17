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

Budget about one minute for the focused profile. The full calibrated suite at
`e3d696f` took 52.63 minutes locally, including discovery and fixture preparation,
on an AMD Ryzen 9 9950X with Python 3.11.15 and numerical-library threads set to
one. The earlier suite at `cdf5b38` took 116.05 minutes on the same machine.
Dependency installation and report publication take additional time, and runtime
varies by machine. The measured run leaves 67.37 minutes against CI's unchanged
120-minute job limit; hosted-runner headroom still needs confirmation in CI.

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
| Preparation | Input validation, rejection, accepted-plan validation, and immutable computation identities |
| Execution | Chains, shared dependencies, batch location acquisition, root and receiving-input routes, explicit location operations, and worker execution |
| Caching | Empty and populated relational caches and revision invalidation |
| Data | Point-location construction/export, mixed nullable tables, polygon data, relational row conversion, and result serialization |
| Interfaces | REST routes, Model Context Protocol (MCP) tools, and retained result previews |
| Memory and size | Whole-process peak memory and serialized preview bytes |

The point-location and scalar-codec cases use 10,000 and 100,000 rows. Storage scenarios range from 16 to 10,000 rows; graph execution includes one-row and 1,000-row inputs. Each scenario selects representative parameters without testing every possible combination.

Timing excludes fixture preparation and includes production validation within the measured operation. Peak memory includes imported libraries, fixtures, retained inputs, and execution. Compare memory results with those setup costs in mind.

Use the receiving-route benchmarks to measure conversions at dependency inputs:

| Suite | Cases | Timed work |
| --- | --- | --- |
| `ReceivingExecutionSuite` | Direct forwarding or an explicit typed filter before conversion | Execute the prepared graph, including acquisition and all handlers; exclude request preparation |
| `ReceivingWorkerSuite` | An explicit typed filter before conversion | Revalidate the serialized accepted request, execute the graph, and persist the result |

Both suites use 1 or 1,000 source records and in-process providers with caching off. These workloads start their measurement history at version 1 because receiving routes have no pre-repair baseline.

Fixtures use deterministic data and repeatable handlers. In-process providers and queue services keep external acquisition offline, so benchmarks need no live services or credentials.

Persistence benchmarks use production SQLite storage in disposable directories. Pytest mocks database acquisition too, so those tests don’t verify SQLite’s own constraint enforcement.

REST measurements use an in-process ASGI transport. MCP measurements cover tool dispatch and output serialization. Both exclude network traffic, client initialization, and event-loop initialization from timing. MCP measurements also exclude protocol session setup.

Interpret cache cases according to their prepared state:

- **Empty or invalidated cache**: reset only disposable benchmark storage before each sample, execute once, and disable warmup
- **Populated cache**: populate entries before timing

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
| Typed scalars | 10,000 | Strings, signed 64-bit integers, booleans, and finite 64-bit floats, including nulls; categorical labels and timestamps use strings |
| Varied points | 10,000 | Distinct 2D coordinates |
| Polygons with holes | 1,000 | 64 exterior vertices and a 16-vertex hole |
| Multipolygons | 1,000 | Three components, each with 32 exterior vertices and an 8-vertex hole |
| Complex polygons, small batch | 16 | 1,024 exterior vertices and a 64-vertex hole |
| Complex polygons, large batch | 128 | 1,024 exterior vertices and a 64-vertex hole |
| Mixed geometries | 1,200 | Six geometry types, with equal 2D and 3D representation |

Vertex counts exclude the closing coordinate, which repeats the first vertex. Spatial scenarios share CRS `EPSG:4326` and the scalar control’s property columns. Timestamp strings use Coordinated Universal Time (UTC).

Timing excludes fixture preparation. `StorageSuite` measures frame copying and row conversion without database input/output (I/O). Execution and worker cases include SQLite persistence.

When comparing workloads, record whether timing includes these operations:

- Frame and row conversion
- Database transactions and commits
- Producer validation
- Result-description publication

Interpret the conversion measurements as follows:

- **Ownership**: copies a complete native frame and its metadata
- **Round trip**: converts validated native columns to relational cells and reconstructs an owned frame
- **To/from rows**: measures each direction of relational conversion independently
- **Memory**: measures the process’s peak memory for typed scalars, points, and complex polygons, including imports and fixture preparation

To save a baseline and repeat the storage measurements, follow the [measurement commands](#save-and-compare-measurements) in this order:

1. Capture the full suite from a clean commit with `--record-samples`.
2. Archive the first pass, then repeat the storage profile with `--append-samples`.
3. Save the commands, environment metadata, raw samples, and benchmark definitions under `.asv/baselines/baseline_commit_sha/`. Replace `baseline_commit_sha` with the measured commit’s hash.

Use the duration report to budget subsequent comparisons. Keep the same environment and workload definitions when comparing results.

## Measure partition reuse and hybrid graphs

Use these benchmarks to compare execution and retrieval when requested locations have a mix of saved and missing results. Each [primary partition](../docs/src/content/docs/concepts/caching.md#complete-and-per-location-computations) contains the output rows for one primary location.

`node_cache.NodeCacheSuite` version 1 requests 100 partitions per execution. It uses one execution per sample with warmup disabled. Setup creates a disposable store and prepares one of these cache states:

| State | Preparation | Measured request |
| --- | --- | --- |
| Cold | Leave the store empty | Request locations 0–99 |
| Warm | Compute locations 0–99 | Request locations 0–99 again |
| Partial | Compute locations 0–99 | Request locations 50–149; compute missing locations 100–149 |
| Invalidated | Compute locations 0–99, then change the root metric’s code revision | Request locations 0–99 with the new revision |

The direct graph contains one per-location metric. The hybrid graph adds an operation that maps each source to one target. It also includes an unpartitioned count over fixed reference records X/Y, with `cache="full"`. Changing the root revision leaves upstream identities unchanged.

Timing includes request acceptance, cache lookup, handlers, publication, and result assembly. Setup prepares the request and cache state before timing begins.

Two retrieval workloads measure assembly and grouped reads separately:

| Workload | Measured work | Fixture setup |
| --- | --- | --- |
| `node_cache.AssemblySuite` version 1 | Reopen a result reference and reconstruct 100 partitions, half reused from an earlier request | Execute both requests and publish the result |
| `ScopedMetricSuite.peakmem_groups` | Read ten indexed groups and measure process peak memory | Publish the fixture before the measured call; its memory still contributes to the peak |

Run calibrated measurements for these workloads and the existing cache suites:

```sh
uv run asv run -E existing --dry-run --durations all \
  --bench 'node_cache' \
  --bench 'execution.CacheSuite|execution.CanonicalCacheSuite' \
  --bench 'sqlite_foundation.ScopedMetricSuite.*groups'
```

These dry runs measure your current checkout without saving results. Follow [Save and compare measurements](#save-and-compare-measurements) to retain samples from a clean checkout. If you export samples from an uncommitted checkout separately, include a source snapshot and environment metadata. Identify them as working-tree measurements rather than measurements of the parent commit.

The partial, hybrid, assembly, and grouped-read memory cases start new measurement histories. Existing grouped-read timings retain their version 2 inputs. Compare the existing cache suites only when their inputs, prepared states, and timing boundaries remain unchanged.

## Measure retained location inputs

The permanent retained-input suite contains 94 cases: 46 timings, 18 process
memory peaks, and 30 database-file size measurements. Direct fixtures share
independent references. Dense fixtures map each source to the same 100 targets,
with full and per-location operation policies and zero independent references.
Combined fixtures exercise both dense policies with 100 sources and 10,000
independent references. All providers are deterministic and in process.

| Measurement | Fixtures | Cases |
| --- | --- | ---: |
| Cached execution and description | 100/1,000 sources; direct 100/10,000 references and both dense policies | 16 |
| Cold execution | All four 1,000-source fixtures | 4 |
| 90%-warm execution | Direct 100/10,000 and both dense 100-source fixtures; direct 1,000/100 | 4 |
| SQL candidates and encoded 100-row pages | Direct 1,000/10,000 and both dense 1,000-source fixtures | 6 |
| Complete native and Arrow retrieval | The same three retrieval fixtures | 6 |
| Provider revision and reference selection invalidation | Direct 100/10,000 and both dense 100-source fixtures; revision changes or one reference is added | 6 |
| Combined cached and mixed execution | Both policies, 100 sources, 10,000 references | 4 |
| Peak memory | Three largest retrieval fixtures: cached, description, native, stream; three small representative fixtures: cold, mixed | 18 |
| Total file bytes and growth | Direct 10,000 references and both dense policies; 100 sources: cold, cached, mixed; 1,000 sources: cold, cached | 30 |

A mixed request starts with 90% of sources already published. Per-location
operations compute missing partitions; the full policy can invalidate all
partitions. Cold means an empty application store, with normal SQLite durability
and operating-system caches.

ASV prepares three cache groups in separate processes: small canonical fixtures,
large canonical fixtures, and combined fixtures. Each group builds all completed seeds once per run. The small group builds
90%-warm seeds only for direct/10,000 references and both dense policies; the
large group builds only the direct/100-reference mixed seed. The combined group
builds both mixed seeds. Timing, memory, and storage adapters share the group's
hook. Selecting a group prepares all its fixtures and storage outcomes, even when
only one dependent benchmark is selected. Small cases never prepare large seeds.
Cold timing and cold-memory cases have no seed hook.

Every measured sample receives a disposable copy of a closed seed. Completed
seeds retain their single-publication history; mixed seeds retain their separate
90%-warm history. Seeds are never reused across runs or revisions. Copying,
correctness checks, and cleanup are outside timing. Execution measures the whole
runner call; reads include store opening and closing. SQL candidates consume rows;
encoded pages additionally construct and serialize the production HTTP response
without network transport. Arrow streaming consumes and discards every chunk.

Timing uses two independent rounds, one operation per sample, no warmup, and
one to five samples per round with a two-second budget for additional sampling.
A slow operation is allowed to finish beyond that budget. Fast operations receive
more samples automatically as implementations improve. Memory and size metrics
run once. Other benchmark modules retain their own sampling settings.

Memory is whole-process peak RSS, including imports and lightweight sample setup;
it excludes seed creation and post-measurement validation. It is not incremental
allocation. Cold memory includes the actual cold execution. Use the opt-in
`build_scripts.profile_retained_inputs` harness for CPU, SQL, and Python-allocation
diagnostics separately from timing.

Storage preparation records both before/after file sizes from one publication per
fixture and state. Both tracking metrics read those same scalar results; they do
not rerun publication. Sizes include SQLite indexes and free pages after writer
connections close. Cold sizes come from the original completed seeds.

```sh
uv run asv run -E existing --dry-run --durations all \
  --bench '^(retained_inputs|retained_input_scaling)\.'
```

`RETAINED_INPUT_BENCH` selects both modules. `RETAINED_INPUT_SCALING_BENCH` selects
the 39 scaling cases. Select `retained_input_scaling.SmallInputSuite` for a small
sampling check. The fast selector is unchanged. Use `--quick --dry-run` only for
execution checks, and normal runs with `--record-samples` from a clean checkpoint
for saved comparisons. Run without concurrent tests or benchmarks and retain
setup durations, source revision, environment, and thread settings.

Unchanged scaling cases retain their version 1 inputs and measurement boundaries.
Small mixed cases use parameter names with their actual 100-source size.
Complementary cases have distinct identities; compare only matching workloads and
versions. Historical results remain available, but changed fixture histories and
memory boundaries do not establish application speedups. Bump versions when
inputs or measurement boundaries change, including through fixture helpers.

CI continues running the full suite on every push to `main`, with a 120-minute
timeout. Use a completed full-run duration to assess that budget; timing only one
module does not establish the full-suite runtime. No wall-clock or size threshold
is enforced by pytest.

Large cold publication, cached execution, retrieval, and read-memory coverage
remain intact. Representative small fixtures cover invalidation and most partial
reuse; the large direct/100-reference case covers reuse across 1,000 partitions.
Large mixed storage measurements are omitted, avoiding unused mixed seeds.
`IdentitySuite` prepares its 10- and 1,000-node graphs once through an ASV setup
cache and selects them outside timing; compilation remains the measured operation.

The calibrated baseline at `cdf5b38cdb3d1dec13831dfe8f3166502014c4bb`
completed all 396 cases across 88 benchmark functions, including all 100
retained-input cases. The 46 retained timings recorded between three and 15 raw
samples each; 35 timings needed only three samples. Each of the three shared
cache groups ran once: small preparation took 1.37 minutes, large preparation
16.18 minutes, and combined preparation 1.72 minutes. These costs are included in
the full-run duration. Raw results, definitions, logs, source snapshots, and
environment metadata are archived locally under
`.asv/baselines/cdf5b38cdb3d1dec13831dfe8f3166502014c4bb/`.

The reduced suite at `e3d696f7d3a88fd9fedccf71d1767a27c8ec9f63` completed
all 390 cases across 90 benchmark functions in 52.63 minutes on the same machine.
All 94 retained measurements were present and finite. The 46 retained timings
recorded two to ten raw samples each; 34 timings needed only two samples. All
three retained cache groups and the identity cache ran once.

| Run cost, minutes | Baseline `cdf5b38` | Reduced suite `e3d696f` |
| --- | ---: | ---: |
| Retained measurements, including per-sample setup and cleanup | 76.31 | 22.68 |
| Small shared seed preparation | 1.37 | 1.29 |
| Large shared seed preparation | 16.18 | 7.85 |
| Combined shared seed preparation | 1.72 | 1.68 |
| Identity measurements, including per-sample setup | 1.61 | 0.05 |
| Shared identity graph preparation | — | 0.47 |
| Other measurements, including per-sample setup | 18.83 | 18.58 |
| Total elapsed, including discovery and orchestration | 116.05 | 52.63 |

The 63.42-minute reduction (54.65%) measures suite cost: smaller representative
workloads, fewer timing rounds, and less repeated preparation. It does not
establish an application performance improvement. Compare application timings
only for matching identities, versions, fixture sizes, prepared states, and
measurement boundaries; account for the changed sample counts when assessing
variation. Other workload definitions and sampling settings remain unchanged.

Source snapshots, environment metadata, commands, smoke and full logs, definitions,
raw samples, setup durations, and the completeness audit are archived locally at
`.asv/baselines/e3d696f7d3a88fd9fedccf71d1767a27c8ec9f63/`.
