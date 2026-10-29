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

This example assumes you have installed `lyra-smoke-plugin` and a location provider plugin named `example-geography`, including their manifests. The provider must declare collection `example:areas` with unit type `region` and resolve ID `area-1`:

```python
from lyra.sdk import LocalRunner

runner = LocalRunner(plugins=["lyra-smoke-plugin", "example-geography"])
tables = runner.run(
    "smoke_table_metric",
    {
        "location": {
            "collection": "example:areas",
            "unit_type": "region",
            "ids": ["area-1"],
        },
        "parameters": {"value": 7},
    },
)
print(tables["smoke_table_metric"])
```

Include every plugin needed by the dependency graph. The runner checks installed versions, manifest identity, and the factory’s definitions when it loads them. Manifests live below `sys.prefix` at `share/lyra/plugins/<normalized-distribution>/lyra.plugin.json`.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. The keys identify plugin ownership. This constructor does not require installed distribution metadata or a generated manifest.

## Supply ID selections

For each named location argument, supply a dictionary or SDK `LocationSelection` with these fields:

- `collection`: the namespaced collection
- `unit_type`: the provider’s unit type
- `ids`: a nonempty list of string IDs

The runner validates all selections, deduplicates IDs, and resolves each selection once through the selected provider. It rejects unknown IDs, IDs of the wrong unit type, and caller-supplied geometry.

When using location inputs, select plugins that declare exactly one provider between them. You can include plugins that provide only locations alongside metric plugins. Requests without location inputs don’t acquire provider resources.

You can run any registered metric directly. The runner resolves the metric’s declared dependencies, then returns a dictionary of native DataFrames or GeoDataFrames. The tables retain their indices, geometry columns, and coordinate reference system (CRS).

Each run keeps an in-memory lookup so repeated dependency invocations share tables. Enable [persistent caching](#reuse-results-across-runs) to reuse results across runs. Construct a new runner and invalidate its cached results after changing plugin definitions.

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

The runner uses one provider resource scope for resolution and closes it before any handler runs. Graph execution uses a separate resource scope for supporting data and other calculation resources, closing it on success or failure. A custom `context_factory` can supply logging, identity, progress, and scratch services; the runner still manages resource cleanup.

## Reuse results across runs

Set `cache_dir` to store validated tables for reuse across runs. Both `LocalRunner` and `LocalRunner.from_definitions` accept this argument. For the installed plugin above, add a cache directory:

```python {5}
from pathlib import Path

runner = LocalRunner(
    plugins=["lyra-smoke-plugin", "example-geography"],
    cache_dir=Path(".lyra-cache"),
)
```

The runner validates and resolves location selections before checking cached graph results on every run. Dependencies reuse the resolved selections without provider lookups.

The runner resolves the path when you create it. Omitting `cache_dir` disables persistent caching. Direct calls to decorated functions execute without the runner’s cache.

Use separate directories for different implementations or resource configurations, and keep source data unchanged while reusing cached results. You manage invalidation and cleanup. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions) for dependency behavior and cache failures.

## Diagnose a local failure

Use the exception type to decide whether to correct the root request, plugin loading, or a dependency’s implementation:

| Exception | Meaning |
| --- | --- |
| `PluginDefinitionError` | The requested metric is unknown |
| `MetricInputError` | The root fields or parameters are invalid |
| `LocationSelectionError` | A named ID selection is invalid |
| `UnresolvedLocationIDsError` | The provider could not resolve requested IDs |
| `LocationAcquisitionError` | Provider acquisition or cleanup failed |
| `LocationProviderContractError` | The provider returned invalid data |
| `GraphExecutionError` | A handler or dependency failed; inspect `node`, `path`, and the chained cause |

Package loading errors identify missing or inconsistent distributions, manifests, or factories. Fix those before rerunning the calculation.

Local execution returns results directly to your code. Your application decides whether to retry a failed run.
