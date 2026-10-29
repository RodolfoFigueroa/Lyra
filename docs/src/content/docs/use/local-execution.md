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

This example assumes `lyra-smoke-plugin` is installed with its manifest and that `region.geojson` contains a polygon FeatureCollection with a declared coordinate reference system (CRS):

```python
import json
from pathlib import Path

from lyra.sdk import LocalRunner

runner = LocalRunner(plugins=["lyra-smoke-plugin"])
tables = runner.run(
    "smoke_table_metric",
    {
        "location": json.loads(Path("region.geojson").read_text()),
        "parameters": {"value": 7},
    },
)
print(tables["smoke_table_metric"])
```

Include every plugin needed by the dependency graph. The runner checks installed versions, manifest identity, and the factory’s definitions when it loads them. Manifests live below `sys.prefix` at `share/lyra/plugins/<normalized-distribution>/lyra.plugin.json`.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. The keys identify plugin ownership. This constructor does not require installed distribution metadata or a generated manifest.

## Supply resolved inputs

Local execution takes resolved SDK inputs. Supply the GeoJSON value directly for `location` or `bounds`; HTTP wrappers such as `{"data_type": "met_zone_code", "value": "09.01"}` belong to the server API.

You can run any registered metric directly. The runner resolves their declared dependencies, then returns a dictionary of native DataFrames or GeoDataFrames. Their indices, geometry columns, and CRS remain intact.

Each run gets a fresh dependency cache. Repeated upstream invocations can share tables within that run. Construct a new runner after changing plugin definitions.

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

The runner initializes each requested resource once per run and closes it on success or failure. A custom `context_factory` can supply logging, identity, progress, and scratch services; the runner still manages resource cleanup.

## Diagnose a local failure

Use the exception type to decide whether to correct the root request, plugin loading, or a dependency’s implementation:

| Exception | Meaning |
| --- | --- |
| `PluginDefinitionError` | The requested metric is unknown |
| `MetricInputError` | The root input is invalid |
| `GraphExecutionError` | A handler or dependency failed; inspect `node`, `path`, and the chained cause |

Package loading errors identify missing or inconsistent distributions, manifests, or factories. Fix those before rerunning the calculation.

Local execution returns directly to your caller. Persistent storage and retries, when needed, belong to the application using the runner.
