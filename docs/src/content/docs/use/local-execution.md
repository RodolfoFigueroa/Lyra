---
title: Run an installed plugin locally
description: Run a plugin from a Python script or notebook and inspect its result tables.
contentType: How-to
contentPlan: use/local-execution
sidebar: { label: "Run An Installed Plugin Locally" }
---

Use `LocalRunner` to call an installed plugin in your Python environment and receive its tables directly. It runs trusted plugin code in your Python process, and your call waits until the calculation finishes. If you haven’t run a metric yet, start with [the first local tutorial](../../start/local/).

You need the software development kit (SDK), selected plugins, and their calculation dependencies in the same Python environment. Follow [Install Lyra for Python](../../start/installation/) if you haven’t prepared that environment yet.

## Load an installed plugin

Install the example plugin and SDK from the same checkout into your uv-managed project. Replace `/your_lyra_checkout_here` with the checkout’s absolute path:

```sh
uv add /your_lyra_checkout_here/packages/lyra_sdk \
  /your_lyra_checkout_here/examples/city-plugin
```

If you completed the [city tutorial](../../plugins/tutorial/package/), generate and check its manifest before installing that project. For a copy outside the checkout, replace the second path with your completed project’s directory. Its public factory must export your tutorial implementation.

`lyra-city-example` includes a manifest describing its calculations and a provider that loads street data from bundled files. The provider turns the street IDs in your request into geometry and properties without a database. The plugin declares these dataset details:

| Field | Value |
| --- | --- |
| Collection | `example:city` |
| Unit type | `street` |
| Source | `bundled-survey` |
| Geographical edition | `layout_v1` |

Load the installed plugin and request the maintenance calculation for `street-1`. The `streets` entry identifies the dataset, feature type, and IDs; `parameters` sets the condition threshold:

```python title="run_installed.py"
from lyra.sdk import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
tables = runner.run(
    "city_street_maintenance",
    {
        "streets": {
            "collection": "example:city",
            "unit_type": "street",
            "ids": ["street-1"],
        },
        "parameters": {"condition_threshold": 50},
    },
)
print(tables["maintenance"])
```

Save this as `run_installed.py` and run `uv run python run_installed.py`. The `maintenance` table contains `street-1`, with `length_m` and `maintenance_length_m` both equal to 100. Its condition score is 20, below the threshold of 50, so its entire length counts as maintenance workload.

Include every plugin needed by the dependency graph. The runner checks installed versions, manifest identity, and the factory’s definitions when it loads them. The runner finds manifests under `sys.prefix` in `share/lyra/plugins/your_distribution_name_here/lyra.plugin.json`. Replace `your_distribution_name_here` with the normalized distribution name.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. Each mapping key identifies the plugin that registers those definitions. This constructor does not require installed distribution metadata or a generated manifest.

## Supply ID selections

For each named location argument, supply a dictionary or SDK `LocationSelection` with these fields:

- `collection`: the dataset identifier, such as `example:city`
- `unit_type`: the feature type, such as `street` or `block`
- `ids`: a nonempty list of string IDs

The runner validates selections and removes duplicate IDs before resolving each selection once through its provider. It rejects these inputs:

- Unknown IDs
- IDs of the wrong unit type
- Geometry supplied by the caller

When using location inputs, select plugins that declare exactly one provider between them. You can include plugins that provide only locations alongside metric plugins. Requests without location inputs don’t acquire provider resources.

Run public metrics through `runner.run()`. For a [family with named editions](../../plugins/authoring/#define-metric-editions), pass `edition="your_edition_here"`, even when only one edition exists. Put settings such as the condition threshold in the input dictionary’s `parameters` entry.

A metric may accept a selection different from the locations it calculates on, such as blocks for a street report. Lyra finds a [canonical conversion](../../concepts/selection/) using operations the plugin author has designated for public selections. It supplies the resulting streets to the metric.

Your request chooses locations, not the operations to run. Lyra checks the request and fixes the conversion route and data revisions before loading data.

## Inspect results and execution details

`runner.run()` returns a dictionary of native DataFrames or GeoDataFrames after executing the metric and its dependencies. These tables retain declared data types, geometry columns, and coordinate reference system (CRS). Lyra normalizes indexes to a fresh `RangeIndex`. [Align rows using declared identifier columns](../results/#understand-row-identity).

Call `runner.inspect()` separately to inspect provenance, the record of accepted inputs and observed execution. Use it to confirm the intended selection and see which results Lyra reused. It includes:

- **Accepted bindings**: the chosen metric, parameter values after applying defaults, selected locations, conversion routes, and data revisions
- **Native input summaries**: counts and fingerprints of the location data supplied to the calculation; fingerprints identify the contents used
- **Execution observations**: which metrics and location operations ran, and which reused saved results

Inspection returns `None` before the first run or after request preparation fails. After an execution error, it retains the failed run’s provenance.

To repeat the city tutorial’s handoff request, change the selection’s `unit_type` to `block` and its `ids` to `["block-1", "block-2"]`. The same metric returns three street rows with maintenance lengths 100 m, 0 m, and 300 m. Follow [inspection in the city tutorial](../../plugins/tutorial/testing/) to interpret its accepted block selection and native street inputs. A planned dependency may not execute when Lyra reuses cached results.

For development and tests, use `runner.run_development(concrete_metric, inputs)` with resolved location values. This method also accepts private metrics and executes dependencies that plugin authors declare using noncanonical operations.

Each run keeps an in-memory lookup so repeated invocations share validated tables and operation locations. Enable [persistent caching](#reuse-results-across-runs) to reuse results across runs. Construct a new runner and invalidate its cached results after changing plugin definitions.

## Supply runtime services

The default runtime context identifies the run and metric. It also supplies logging, progress reporting, and a shared temporary directory. The runner removes that directory before `run()` returns, on both success and failure. Return data that no longer depends on scratch files.

Resources are the data clients or other services a plugin needs during a run. Configure them by name with the `resource_config` argument. Both `LocalRunner(...)` and `LocalRunner.from_definitions(...)` accept a mapping of names to `ResourceConfig` objects.

For the [Parquet resource example](../../plugins/runtime/#define-an-io-resource), use its `plugin` definition and set the directory containing `observations.parquet`:

```python {5-7}
from lyra.sdk import LocalRunner, ResourceConfig

runner = LocalRunner.from_definitions(
    {"example": plugin},
    resource_config={
        "shared_files": ResourceConfig(config={"directory": "/data"}),
    },
)
tables = runner.run("observations", {})
```

Pass fake resource classes through `resource_overrides` when testing. Each fake must subclass the registered resource class. The [city graph test](../../plugins/testing/#test-the-complete-graph) shows how to replace provider acquisition with a mock.

The runner shares resources between location lookups and calculations during a run. It initializes them when needed and closes them when the run ends, including on failure. Pass a custom `context_factory` to replace runtime services. The runner still manages resource cleanup.

## Reuse results across runs

If you repeat a calculation, set `artifact_dir` to save validated results for later runs. Lyra can reuse metric tables and locations produced by operations when their inputs and revisions match. Both `LocalRunner` and `LocalRunner.from_definitions` accept this argument. For the installed plugin above, set the artifact directory:

```python {5}
from pathlib import Path

runner = LocalRunner(
    plugins=["lyra-city-example"],
    artifact_dir=Path(".lyra-artifacts"),
)
```

Every run checks your selections and loads their locations before looking for saved calculation results. Dependencies reuse those loaded locations. An operation may discover additional IDs. Lyra loads them through the same provider before checking the receiving calculation’s cache.

The runner resolves the path when you create it. Omitting `artifact_dir` disables persistent caching. Direct calls to decorated functions execute without the runner’s cache.

Persistent caches store metric tables and operation locations in [Arrow interprocess communication (IPC) artifacts](../../concepts/caching/#store-and-validate-cached-results). Lyra validates restored outputs against the current producer schemas before consumers run.

Before execution, Lyra records the declared location revisions. If those revisions change, Lyra cannot reuse the affected calculations’ previous results.

For changes those revisions do not track, pass a new `cache_revision` to `runner.run()`, such as `cache_revision="dataset-2026-10"`. Use this to force recomputation or account for code changes that have no version update.

The `cache_revision` value applies to the whole run alongside the declared revisions. Changing it leaves older cached results in place. Recording revisions does not create data snapshots or guarantee fresh data. Read the [revision coverage and limits](../../plugins/location-providers/#define-revision-coverage) before relying on persistent reuse.

Processes sharing an artifact directory must use equivalent code and resource configurations. You manage invalidation and cleanup. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions) for dependency behavior and cache failures.

## Diagnose a local failure

Use the exception type to decide whether to correct the root request, plugin loading, or a dependency’s implementation:

| Exception | Meaning |
| --- | --- |
| `PreparationError` (a `MetricInputError`) | The metric, edition, parameters, selections, or cache revision are invalid; execution has not started |
| `PluginDefinitionError` | A plugin definition is invalid, or a development call names an unknown metric |
| `GraphExecutionError` caused by `UnresolvedLocationIDsError` | The provider could not resolve requested IDs during execution |
| `GraphExecutionError` caused by `LocationAcquisitionError` | Provider acquisition failed |
| `GraphExecutionError` caused by `LocationProviderContractError` | The provider returned invalid data during execution |
| `GraphExecutionError` | Resolution, cleanup, a handler, or a dependency failed; inspect `node`, `path`, and the chained cause |

Package loading errors identify missing or inconsistent distributions, manifests, or factories. Fix those before rerunning the calculation.

Local execution returns results directly to your code. Your application decides whether to retry a failed run.
