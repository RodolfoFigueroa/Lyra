---
title: Run an installed plugin locally
description: Run a plugin from a Python script or notebook and inspect its result tables.
contentType: How-to
contentPlan: use/local-execution
sidebar: { label: "Run An Installed Plugin Locally" }
---

Use `LocalRunner` to call an installed plugin in your Python environment and receive its tables directly. It runs trusted plugin code in your Python process, and your call waits until the calculation finishes. If you haven’t run a metric yet, start with [the first local tutorial](../../start/local/).

Install `lyra[runtime]` in the same Python environment as your plugin and its dependencies. For checkout and Python requirements, see [Install Lyra for Python](../../start/installation/).

## Load an installed plugin

From your uv project, install Lyra and the city plugin from the same checkout. Replace `/your_lyra_checkout_here` in both paths with the absolute path to your checkout.

```sh
uv add --extra runtime /your_lyra_checkout_here
uv add /your_lyra_checkout_here/examples/city-plugin
```

If you completed the [city tutorial](../../plugins/tutorial/package/), export its entry-point factory before installing that project. For a copy outside the checkout, replace the second path with your completed project’s directory. Its public factory must export your tutorial implementation.

`lyra-city-example` registers live calculation definitions and a provider that loads street data from bundled files. The provider turns the street IDs in your request into geometry and properties without a database. The plugin declares these dataset details:

| Field | Value |
| --- | --- |
| Provider ID | `city_streets` |
| Provider code revision | `1` |
| Origin ID | `example-city` |
| Origin revision | `fixture_1` |

Load the installed plugin and request the maintenance calculation for `street-1`. The `streets` entry identifies the provider and record IDs; `parameters` sets the condition threshold:

```python title="run_installed.py" {6-13}
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
tables = runner.run(
    "city_street_maintenance",
    inputs={
        "streets": {
            "provider": "city_streets",
            "ids": ["street-1"],
        }
    },
    parameters={"condition_threshold": 50},
).tables
print(tables["maintenance"])
```

Save this as `run_installed.py` and run `uv run python run_installed.py`. The `maintenance` table contains `street-1`, with `length_m` and `maintenance_length_m` both equal to 100. Its condition score is 20, below the threshold of 50, so its entire length counts as maintenance workload.

Include every plugin needed by the dependency graph. The runner loads each selected installed distribution through its `lyra.plugins` entry point named `plugin`.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. Each mapping key identifies the plugin that registers those definitions.

## Supply ID selections

For each named location argument, supply a JSON-compatible dictionary with these fields:

- `provider`: the public provider ID, such as `city_streets`
- `ids`: a list of nonempty string IDs; the list may be empty

The runner checks whether the input accepts the provider before loading its data. Supply IDs from that provider and omit geometry from the request.

Lyra passes distinct IDs to the provider. Align records by identifier columns because retrieval doesn’t guarantee row order. An unknown ID fails the run during resolution.

Enable the plugins that register the providers your calculation needs. Each public provider ID must have exactly one registration across those plugins. You can include provider-only plugins alongside metric plugins.

Call `runner.run()` with the registered public metric name. Supply named selections through `inputs=` and calculation settings, such as the condition threshold, through `parameters=`.

Omitting either mapping, or passing Python `None`, uses an empty mapping before Lyra applies defaults and validates required values. To reuse a request dictionary containing `inputs` and `parameters`, unpack it with `runner.run(metric, **request)`.

For a street report, the city plugin also accepts block IDs. Before loading data, Lyra selects the [canonical route](../../concepts/selection/) and captures the declared provider scopes. It then resolves the selected blocks to streets and passes those streets to the metric.

Before loading data, the executor validates your selected inputs and calculation dependencies against the loaded plugin definitions. These checks also apply before cache reuse and when you run private metrics with `run_development()`.

## Inspect results and execution details

`runner.run()` returns a `RunResult` after executing the metric and its dependencies. Access native DataFrames or GeoDataFrames through `result.tables[name]` and execution descriptions through `result.metadata`. The metadata includes the concrete metric name, effective parameters, and each output's scope, table schema, and owning provider scope. These tables retain declared data types, geometry columns, and CRS. Lyra normalizes indexes to a fresh `RangeIndex`. [Align rows using declared identifier columns](../results/#understand-row-identity).

Call `runner.inspect()` separately to inspect provenance, the record of accepted inputs and observed execution. Use it to confirm the intended selection and see which results Lyra reused. It includes:

- **Accepted bindings**: the chosen metric, parameter values after applying defaults, selected locations, conversion routes, and data revisions
- **Native input summaries**: counts of the location records supplied to the calculation
- **Execution observations**: which metrics and location operations ran, and which reused saved results

Inspection returns `None` before the first run or after request preparation fails. After an execution error, it records the failed run’s provenance without retaining observations from your previous run.

To repeat the city tutorial’s handoff request, change the selection’s `provider` to `city_blocks` and its `ids` to `["block-1", "block-2"]`. The same metric returns three street rows with maintenance lengths 100 m, 0 m, and 300 m. Follow [inspection in the city tutorial](../../plugins/tutorial/testing/) to interpret its accepted block selection and native street inputs. A planned dependency may not execute when Lyra reuses cached results.

To test a private metric, call `runner.run_development()` with its name and the same `inputs=` and `parameters=` keywords. Lyra validates its selections and parameters before execution. Dependencies can use author-declared noncanonical operations.

Each run keeps an in-memory lookup so repeated graph references share validated tables and operation mappings. [Persistent storage](#reuse-results-across-runs) retains final outputs automatically; node cache policies control reuse across runs.

## Supply runtime services

The default runtime context identifies the run and metric. It also supplies logging, progress reporting, and a shared temporary directory. The runner removes that directory before `run()` returns, on both success and failure. Return data that no longer depends on scratch files.

Resources are the data clients or other services a plugin needs during a run. Configure them by name with the `resource_config` argument. Both `LocalRunner(...)` and `LocalRunner.from_definitions(...)` accept a mapping of names to `ResourceConfig` objects.

For the [Parquet resource example](../../plugins/runtime/#define-an-io-resource), use its `plugin` definition and set the directory containing `observations.parquet`:

```python {5-7}
from lyra.runtime.local import LocalRunner
from lyra.contracts.resources import ResourceConfig

runner = LocalRunner.from_definitions(
    {"example": plugin},
    resource_config={
        "shared_files": ResourceConfig(config={"directory": "/data"}),
    },
)
tables = runner.run("observations").tables
```

Pass fake resource classes through `resource_overrides` when testing. Each fake must subclass the registered resource class. The [city graph test](../../plugins/testing/#test-the-complete-graph) shows how to replace provider acquisition with a mock.

The runner shares resources between location lookups and calculations during a run. It initializes them when needed and closes them when the run ends, including on failure. Pass a custom `context_factory` to replace runtime services. The runner still manages resource cleanup.

## Reuse results across runs

Every successful managed run saves required final outputs automatically. The default store is `.lyra/results.sqlite3` under the working directory. To choose another file:

```python {6}
from pathlib import Path
from lyra.runtime.local import LocalRunner

runner = LocalRunner(
    plugins=["lyra-city-example"],
    result_store_path=Path("analysis/results.sqlite3"),
)
```

For definitions already in memory, use the same keyword:

```python {3}
runner = LocalRunner.from_definitions(
    {"example": plugin},
    result_store_path=Path("analysis/results.sqlite3"),
)
```

Both constructors resolve default and explicit relative paths to absolute paths when you create the runner. Changing the working directory afterward doesn’t move that runner’s store.

During execution, Lyra creates the parent directory and initializes storage when needed. Choose a persistent writable directory outside job scratch space. SQLite also needs permission to create journal files beside the database.

`run()` and `run_development()` follow the same persistence rules, including private metrics and empty results. Retain `result.metadata.store_path` and `result.metadata.result_ref` together to [reopen results](../results/#reopen-a-local-result) without a runner or plugins. Each successful run gets its own reference, even when it reuses stored values.

Each node’s [cache policy](../../concepts/caching/) determines whether Lyra can reuse its saved values. A node with caching off executes when invoked, even if Lyra has saved its final output.

With caching enabled, Lyra reuses values when declared identities and effective inputs match. A complete cache hit skips upstream acquisition that the result no longer needs. `run_development()` follows the same policies and can reuse saved values.

Lyra retains written values indefinitely, including after identities or cache settings change. Recreate the runner after changing plugin definitions, and update the relevant revision when outputs change. Calls to decorated functions outside the runner don’t save results automatically.

## Diagnose a local failure

Use the exception type to decide whether to correct the root request, plugin loading, or a dependency’s implementation:

| Exception | Meaning |
| --- | --- |
| `PreparationError` (a `MetricInputError`) | The metric, parameters, or selections are invalid; execution has not started |
| `PluginDefinitionError` | A plugin definition is invalid |
| `GraphExecutionError` caused by `UnresolvedLocationIDsError` | The provider could not resolve requested IDs during execution |
| `GraphExecutionError` caused by `LocationAcquisitionError` | Provider acquisition failed |
| `GraphExecutionError` caused by `LocationProviderContractError` | The provider returned invalid data during execution |
| `GraphExecutionError` | Resolution, cleanup, a handler, or a dependency failed; inspect `node`, `path`, and the chained cause |

Package loading errors identify missing or inconsistent distributions, entry points, or factories. Fix those before rerunning the calculation.

Local execution returns results directly to your code. Your application decides whether to retry a failed run.
