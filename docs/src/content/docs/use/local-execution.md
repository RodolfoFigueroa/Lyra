---
title: Run an installed plugin locally
description: Execute plugin graphs in your Python process and supply the services they need.
contentType: How-to
contentPlan: use/local-execution
sidebar: { label: "Run An Installed Plugin Locally" }
---

`LocalRunner` runs trusted plugins synchronously in your Python process. Use it in scripts, notebooks, and plugin tests. The [first local tutorial](../../start/local/) introduces the runner with an in-memory definition.

You need the software development kit (SDK), selected plugins, and their calculation dependencies in the same Python environment. Follow [Install Lyra for Python](../../start/installation/) if you haven’t prepared that environment yet.

## Load an installed plugin

Install `lyra-smoke-plugin` and a location provider plugin named `example-geography`, including their manifests. The provider must register `SUBJECT` from `smoke_plugin.locations` and resolve ID `area-1`. That contract defines these bindings:

| Field | Value |
| --- | --- |
| Collection | `test:units` |
| Unit type | `lot` |
| Source | `fixture` |
| Geographical edition | `current` |

Load both plugins and run the metric with a named ID selection:

```python
from lyra.sdk import LocalRunner

runner = LocalRunner(plugins=["lyra-smoke-plugin", "example-geography"])
tables = runner.run(
    "smoke_table_metric",
    {
        "location": {
            "collection": "test:units",
            "unit_type": "lot",
            "ids": ["area-1"],
        },
        "parameters": {"value": 7},
    },
)
print(tables["smoke_table_metric"])
```

Include every plugin needed by the dependency graph. The runner checks installed versions, manifest identity, and the factory’s definitions when it loads them. The runner finds manifests under `sys.prefix` in `share/lyra/plugins/your_distribution_name_here/lyra.plugin.json`. Replace `your_distribution_name_here` with the normalized distribution name.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. The keys identify plugin ownership. This constructor does not require installed distribution metadata or a generated manifest.

## Supply ID selections

For each named location argument, supply a dictionary or SDK `LocationSelection` with these fields:

- `collection`: the namespaced collection
- `unit_type`: the provider’s unit type
- `ids`: a nonempty list of string IDs

The runner validates all selections, deduplicates IDs, and resolves each selection once through the selected provider. It rejects unknown IDs, IDs of the wrong unit type, and caller-supplied geometry.

When using location inputs, select plugins that declare exactly one provider between them. You can include plugins that provide only locations alongside metric plugins. Requests without location inputs don’t acquire provider resources.

Run public metrics through `runner.run()`. For a [family with named editions](../../plugins/authoring/#define-metric-editions), pass `edition="your_edition_here"`, even when only one edition exists. Put ordinary parameters in the input mapping’s `parameters` entry.

The runner selects a canonical route to convert each selection into the metric’s required input. Routes use operations that plugin authors mark as canonical. Your request cannot name operations or supply a pipeline. The runner validates the request and binds its routes and revisions before accessing data.

## Inspect results and execution details

`runner.run()` returns a dictionary of native DataFrames or GeoDataFrames after executing the metric and its dependencies. These tables retain declared data types, geometry columns, and coordinate reference system (CRS). Lyra normalizes indexes to a fresh `RangeIndex`. [Align rows using declared identifier columns](../results/#understand-row-identity).

Call `runner.inspect()` separately to inspect provenance, the record of accepted inputs and observed execution. It includes:

- **Accepted bindings**: the selected metric, effective parameters, source selections, routes, and pinned revisions
- **Native input summaries**: counts and content fingerprints for the resolved inputs
- **Execution observations**: which nodes executed or reused cached results

Inspection returns `None` before the first run or after request preparation fails. After an execution error, it retains the failed run’s provenance.

The service-free [capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) runs the `double` edition of `street_rating`. It selects blocks and street context independently, then reads `tables["scores"]` and calls `runner.inspect()`. A planned dependency may not execute when Lyra reuses cached results.

For development and tests, use `runner.run_development(concrete_metric, inputs)` with resolved location values. This method also accepts private metrics and executes dependencies that plugin authors declare using noncanonical operations.

Each run keeps an in-memory lookup so repeated invocations share validated tables and operation locations. Enable [persistent caching](#reuse-results-across-runs) to reuse results across runs. Construct a new runner and invalidate its cached results after changing plugin definitions.

## Supply runtime services

The default context identifies the run and metric, logs diagnostics and progress, and gives metrics a shared temporary directory. The runner removes that directory before `run()` returns, on both success and failure. Return data that no longer depends on scratch files.

Configure named resources with the `resource_config` argument. Both `LocalRunner(...)` and `LocalRunner.from_definitions(...)` accept a mapping of names to `ResourceConfig` objects.

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

Pass fake resource classes through `resource_overrides` when testing. Each fake must subclass the registered resource class. The [database graph test](../../plugins/testing/#test-the-complete-graph) shows how to replace a service client with a mock.

The runner resolves initial selections inside the graph execution resource scope. The runner acquires shared resource instances when resolution or handlers need them. It closes those resources when execution ends, including on failure. A custom `context_factory` can supply logging, identity, progress, and scratch services; the runner still manages resource cleanup.

## Reuse results across runs

Set `cache_dir` to store validated metric tables and operation locations for reuse across runs. Both `LocalRunner` and `LocalRunner.from_definitions` accept this argument. For the installed plugin above, add a cache directory:

```python {5}
from pathlib import Path

runner = LocalRunner(
    plugins=["lyra-smoke-plugin", "example-geography"],
    cache_dir=Path(".lyra-cache"),
)
```

The runner validates and resolves location selections before checking cached graph results on every run. Dependencies reuse initial resolved selections. Operations that discover IDs use the same managed resolver and resource scope; their preparation runs before the receiving invocation’s cache lookup.

The runner resolves the path when you create it. Omitting `cache_dir` disables persistent caching. Direct calls to decorated functions execute without the runner’s cache.

Persistent caches store metric tables and operation locations as uncompressed Arrow interprocess communication (IPC) artifacts. Lyra validates restored outputs against the current producer schemas before consumers run.

Preparation pins declared location revisions, which invalidate affected calculations when they change. For changes outside their coverage, pass a fresh `cache_revision`, such as `cache_revision="dataset-2026-10"`, to `runner.run()`. Use it for deliberate recomputation or unversioned implementation changes too.

The run-wide revision adds to declared revisions and preserves older cache namespaces. Pinning does not create snapshots or guarantee freshness. Read the [revision coverage and limits](../../plugins/location-providers/#define-revision-coverage) before relying on persistent reuse.

Processes sharing a cache directory must use equivalent implementations and resource configurations. You manage invalidation and cleanup. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions) for dependency behavior and cache failures.

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
