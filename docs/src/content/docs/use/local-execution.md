---
title: Local Execution
description: Run plugin graphs directly with the Lyra SDK.
---

`LocalRunner` executes trusted plugins synchronously in the current Python process.
It requires the SDK and the plugin's dependencies, with no Lyra server, queue, or
application configuration. Dataset handlers may still use external sources if
explicitly configured by the caller.

## Installed plugins

Install plugins into your environment before constructing the runner:

```python
from lyra.sdk import LocalRunner

runner = LocalRunner(plugins=["reimagina-test-port"])
tables = runner.run(
    "reimagina_built_form",
    {"parameters": {"snapshot": "synthetic"}},
)
lots = tables["reimagina_built_form_lots"]
```

The runner reads each distribution's manifest from
`share/lyra/plugins/<normalized-distribution>/lyra.plugin.json` beneath the Python
environment. Its installed version, manifest identity, and factory contract must
agree. Factories and the combined graph are loaded once per runner. Every `run()`
has a fresh dependency-result cache; shared upstream nodes execute once within
that invocation. Explicitly include all plugins needed by the dependency graph.

## In-memory definitions

Notebook and development code can supply definitions directly. No installation,
package version, or generated manifest is required:

```python
import pandas as pd
from lyra.sdk import (
    LocalRunner,
    PluginDefinition,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)


@metric(
    name="example_count",
    description="A small local example.",
    outputs={
        "counts": TableOutput(
            columns=[
                TableColumn(
                    name="count", type="integer", unit=Unit.COUNT, description="Count"
                )
            ]
        )
    },
)
def count():
    return pd.DataFrame({"count": [3]})


runner = LocalRunner.from_definitions({"example": PluginDefinition(nodes=[count])})
print(runner.run("example_count", {})["counts"])
```

Mapping keys identify plugin ownership; node and output names remain globally
unique across the runner. Construct a new runner after changing definitions.

## Inputs and results

`run(metric, inputs)` accepts the SDK's node inputs, such as `parameters` and
already-resolved `location` or `bounds` GeoJSON values. It does not resolve
geographic identifiers, perform database lookups, or unwrap HTTP input selectors.
Only public metrics can be execution roots.

Results are dictionaries of native DataFrames/GeoDataFrames, retaining indices,
geometry columns, and CRS. No HTTP envelope, serialization, copying, or automatic
reprojection occurs. Existing argument, graph, and output-shape checks apply;
there is no added validation of producer cell values.

Invalid roots raise `PluginDefinitionError`; invalid root inputs raise
`MetricInputError`. Handler and dependency failures raise `GraphExecutionError`,
with `node`, `path`, and the original chained cause. Loading failures report the
missing or inconsistent package, manifest, or factory.

## Contexts and ownership

Default contexts provide a generated `job_id`, node-specific logger, and a shared
scratch directory for that run. Scratch storage is removed on success and failure,
before `run()` returns. Results must not depend on retained scratch files.
`report_progress()` validates its arguments and logs them using standard Python
logging; applications control logging handlers and levels.

Metrics receive a `RunContext` without database access. Dataset contexts provide
the same local services; accessing `context.db` raises an error unless the caller
supplies a custom context factory. No default database connection is created.

Both constructors accept `context_factory(node_name, role)`, where `role` is
`"metric"` or `"dataset"`. It must return an appropriate context, and is called
only for handlers declaring a context parameter. It replaces default context
construction entirely. The caller owns the custom contexts, connections, progress
reporting, and temporary storage, including cleanup on failure. Metric contexts
must not expose `db`. Close caller-owned services explicitly outside the runner.

Local execution has no retained jobs, background execution, automatic retries,
or persistent cache. It does not require a server to perform calculations.
