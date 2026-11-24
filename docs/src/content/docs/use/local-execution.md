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

```python title="run_installed.py" {6-13,15-18}
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
result = runner.run(
    "city_street_maintenance",
    inputs={
        "streets": {
            "provider": "city_streets",
            "ids": ["street-1"],
        }
    },
    parameters={"condition_threshold": 50},
)
tables = result.tables
print(tables["maintenance"])
print(result.metadata.execution_id)
print(result.report.request.parameters)
assert result.report.result_ref == result.metadata.result_ref
```

Save this as `run_installed.py` and run `uv run python run_installed.py`. The `maintenance` table contains `street-1`, with `length_m` and `maintenance_length_m` both equal to 100. Its condition score is 20, below the threshold of 50, so its entire length counts as maintenance workload.

Include every plugin needed by the dependency graph. The runner loads each selected installed distribution through its `lyra.plugins` entry point named `plugin`.

For definitions you already have in memory, use `LocalRunner.from_definitions({"example": plugin})`. Each mapping key identifies the plugin that registers those definitions.

## Supply ID selections

For each named location argument, supply a JSON-compatible dictionary with these fields:

- `provider`: the public provider ID, such as `city_streets`
- `ids`: a list of nonempty string IDs; the list may be empty

The runner checks whether the input accepts the provider before loading its data. Supply only the provider ID and record IDs in the selection.

Lyra passes distinct IDs to the provider. Align records by identifier columns because retrieval doesn’t guarantee row order. An unknown ID fails the run during resolution.

Enable the plugins that register the providers your calculation needs. Each public provider ID must have exactly one registration across those plugins. You can include provider-only plugins alongside metric plugins.

Call `runner.run()` with the registered public metric name. Supply named selections through `inputs=` and calculation settings, such as the condition threshold, through `parameters=`.

Omitting either mapping, or passing Python `None`, uses an empty mapping before Lyra applies defaults and validates required values. To reuse a request dictionary containing `inputs` and `parameters`, unpack it with `runner.run(metric, **request)`.

For a street report, the city plugin also accepts block IDs. Before loading data, Lyra selects the [canonical route](../../concepts/selection/) and captures the declared provider scopes. It then resolves the selected blocks to streets and passes those streets to the metric.

Before loading data, the executor validates your selected inputs and calculation dependencies against the loaded plugin definitions. These checks also apply before cache reuse and when you run private metrics with `run_development()`.

## Run with a street proposal

To measure a proposed street, prepare its complete record in EPSG:32614, the city provider’s CRS. Give the street a new `location_id` and list the surveyed street’s ID in `remove`. Lyra reads IDs from the column and ignores the frame index.

```python title="street_proposal.py" {7-9,15-16}
import geopandas as gpd
from shapely.geometry import LineString

from lyra.data.interventions import Intervention

additions = gpd.GeoDataFrame(
    {"location_id": ["proposed-1"], "condition_score": [20.0]},
    geometry=[LineString([(500050, 2100000), (500050, 2100150)])],
    crs="EPSG:32614",
)
proposal = Intervention(
    catalog="city_streets",
    id="street-extension",
    revision="1",
    add=additions,
    remove=["street-1"],
)
```

Select `proposed-1` in the `streets` input and pass the proposal to `run()`. You can pass the same `interventions` argument to `run_development()`.

```python title="street_proposal.py" {3,8}
from lyra.runtime.local import LocalRunner

inputs = {"streets": {"provider": "city_streets", "ids": ["proposed-1"]}}
runner = LocalRunner(plugins=["lyra-city-example"])
result = runner.run(
    "city_street_lengths",
    inputs=inputs,
    interventions=[proposal],
)
print(result.tables["lengths"])
```

The `lengths` table contains one row for `proposed-1`, with `length_m` equal to 150. Once Lyra accepts the request, you can edit the frame or removal list without affecting that run.

To submit only removals, omit `add` or pass a zero-row GeoDataFrame. Lyra uses the target provider’s schema for either spelling. See [how to prepare changes for a catalog](../spatial-inputs/#prepare-changes-for-a-catalog).

## Inspect results and execution details

Read your tables and execution details from the `RunResult` returned by `runner.run()`. Lyra returns only after saving the required final values and publishing their retrieval description with a complete report.

Use these fields to inspect the result:

- `result.tables[name]`: native DataFrame or GeoDataFrame for a named output
- `result.metadata`: metric name, effective parameters, output descriptions, and addresses for reopening the result
- `result.report`: complete [execution report](../../concepts/provenance/) describing the request, resolved graph, and observed work

The tables preserve declared data types, geometry columns, and CRS. Lyra gives each table a fresh `RangeIndex`; [align rows using declared identifier columns](../results/#understand-row-identity).

Lyra attaches the report before returning, so reading `result.report` performs no storage lookup. Its `status` is `"succeeded"`, and its `execution_id` and `result_ref` match `result.metadata`.

You own the returned tables, metadata, and report. Editing them, including nested values or DataFrame attributes, cannot change stored descriptions or later reads. Ordinary field access doesn’t copy values again, and later runs don’t replace this result’s report.

Call `runner.inspect()` for diagnostics from the latest attempt. Lyra returns an independent copy containing:

- **Accepted bindings**: the chosen metric, parameter values after applying defaults, selected locations, conversion routes, and data revisions
- **Native input summaries**: counts of the location records supplied to the calculation
- **Execution observations**: which metrics and location operations ran, and which reused saved results

Inspection returns `None` before the first run or after request preparation fails. After execution starts, it can include observations collected before an error. A new attempt replaces these diagnostics, so keep `result.report` to inspect a particular successful execution.

Diagnostics use labels such as `completed` and `cache_hit`. The retained report uses [four node outcomes](../../concepts/provenance/#interpret-the-four-outcomes) to summarize execution and reuse.

To repeat the city tutorial’s handoff request, change the selection’s `provider` to `city_blocks` and its `ids` to `["block-1", "block-2"]`. The same metric returns three street rows with maintenance lengths 100 m, 0 m, and 300 m. Follow [inspection in the city tutorial](../../plugins/tutorial/testing/) to interpret its accepted block selection and native street inputs. A planned dependency may not execute when Lyra reuses cached results.

To test a private metric, call `runner.run_development()` with its name and the same `inputs=` and `parameters=` keywords. It returns the same `RunResult`, including the attached report. Lyra validates the request before execution, and dependencies can use author-declared noncanonical operations.

Repeated references within a run share validated tables and operation mappings. [Persistent storage](#reuse-results-across-runs) retains final outputs automatically; node cache policies control reuse across runs. Direct calls to decorated functions don’t create managed reports or save results automatically.

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

Lyra automatically saves required final outputs and the records, operation pairs, and selection references needed for available root location inputs for every successful managed run. It publishes the final description and complete report together in one transaction, so neither becomes available on its own. The default store is `.lyra/results.sqlite3` under the working directory. To choose another file:

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

`run()` and `run_development()` follow the same storage rules, including private metrics and empty results. Each successful run gets its own execution ID and result reference, even when it reuses stored values. Keep `result.metadata.store_path` with both identifiers to [reopen the report or result](../results/#reopen-a-local-result).

Each node’s [cache policy](../../concepts/caching/) determines whether Lyra can reuse its saved values. A node with caching off executes when invoked, even if Lyra has saved its final output.

With caching enabled, Lyra reuses values when declared identities and effective inputs match. A complete cache hit skips upstream acquisition that the result no longer needs. `run_development()` follows the same policies and can reuse saved values.

Changing identities or cache settings doesn’t delete saved results or reports from the [supported store](../../operate/caching/#preserve-stored-results). Recreate the runner after changing plugin definitions, and update the relevant revision when outputs change.

## Diagnose a local failure

Use the exception type to decide whether to correct the root request, plugin loading, or a dependency’s implementation:

| Exception | Meaning |
| --- | --- |
| `PreparationError` (a `MetricInputError`) | The metric, parameters, or selections are invalid; execution has not started |
| `PluginDefinitionError` | A plugin definition is invalid |
| `ExecutionPersistenceError` | Lyra could not save required values or publish the result and report; the run returns no `RunResult` |
| `GraphExecutionError` caused by `UnresolvedLocationIDsError` | The provider could not resolve requested IDs during execution |
| `GraphExecutionError` caused by `LocationAcquisitionError` | Provider acquisition failed |
| `GraphExecutionError` caused by `LocationProviderContractError` | The provider returned invalid data during execution |
| `GraphExecutionError` | Resolution, cleanup, a handler, or a dependency failed; inspect `node`, `path`, and the chained cause |

Package loading errors identify missing or inconsistent distributions, entry points, or factories. Fix those before rerunning the calculation.

Import `ExecutionPersistenceError` from `lyra.contracts.errors` to handle storage failures. Inspect `execution_id` to identify the attempt and `__cause__` for the underlying exception. The ID doesn’t guarantee that a report exists.

Publication failure fails the run even if calculation finished. Complete cache values saved earlier can remain reusable. Lyra retains complete successful reports only; calculation failures keep their existing exception behavior.

Local execution returns results directly to your code. Your application decides whether to retry a failed run.

Read `result.metadata.location_inputs` for captured input descriptions. Use `ResultStore.describe_result()` for metadata without tables or reports, and `read_location_input(result_ref, input_name)` for an explicit native read. [Retained location inputs](../results/#read-retained-location-inputs) describes complete selections across cache hits, known empty inputs, and unavailable skipped work. These rules also apply to `run_development()`. Required input persistence failures prevent successful publication.
