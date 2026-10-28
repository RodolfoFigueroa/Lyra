---
title: Plugin Authoring
description: Wrap existing Python calculations with typed parameters and native results.
---

A metric is a synchronous Python function with a parameter model, spatial inputs,
and a declared output. Keep existing computation code in its own functions or
library and add a small Lyra adapter. The runnable example is
[`examples/lyra-plugin`](https://github.com/RodolfoFigueroa/lyra/tree/main/examples/lyra-plugin).
It contains table and progress metrics and population graphs, plus a generated manifest.
The `smoke_plugin/graph.py` examples use illustrative source views: a fixed-city
dataset forwards `year`, while a client-selected baseline renames `baseline_year`
and returns spatial areas and a total. These views are owned by the example plugin,
not required platform tables.

## Declare a metric

Ordinary inputs belong to one Pydantic model. `MetricParameters` sets
`extra="forbid"` and `validate_default=True`. Use native `Field` declarations for
descriptions, constraints, defaults, and examples. The decorator infers the model
from the `parameters` argument; do not repeat it in a decorator mapping.

This table adapter follows the runnable example, with an optional execution context
used for progress:

```python
import pandas as pd
from pydantic import Field

from lyra.sdk import (
    LocationInput,
    MetricParameters,
    RunContext,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)


class Parameters(MetricParameters):
    value: int = Field(description="Value copied into each output row.")


@metric(
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    outputs={
        "smoke_table_metric": TableOutput(
            columns=[
                TableColumn(
                    name="value",
                    type="integer",
                    unit=Unit.COUNT,
                    description="Submitted value.",
                ),
            ]
        )
    },
)
def run_table(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    context.report_progress(stage="table", current=1, total=1)
    feature_ids = [feature.id for feature in location.features]
    return pd.DataFrame(
        {"value": [parameters.value for _ in feature_ids]},
        index=feature_ids,
    )
```

An adapter can instead pass `parameters.value` to an existing calculation. Use
`lyra.utils.geometry.convert_geojson_to_gdf(location)` if that calculation expects
a GeoDataFrame. The underlying library does not need to accept Lyra objects.

## Describe the metric for consumers

Manifest metadata must let a consumer select a metric, choose meaningful inputs,
and interpret results without reading the README. Begin with a useful summary.
Include a detail only if it changes one of those decisions or interpretations.
Sources, formulas, thresholds, temporal meaning, resolution, and limitations are
possible content, not required categories.

Keep overall meaning in the metric description, input choices and their effects
in parameter descriptions, and individual output meanings and scales in column
descriptions. Explain established missing-value meanings when useful, rather than
saying nulls remain null. Describe temporal meaning when known instead of listing
absent date filters. Source links support essential interpretation but cannot
replace it. Avoid repeating the explanation across fields.

Omit routine reprojection, internal band names, scaling formulas, unused
attributes, and error propagation unless they affect consumer decisions. Preserve
actual input requirements and formulas that define the indicator itself.

Keep known coverage restrictions and consequential interpretation limits, such as
an epoch estimate rather than an exact event date. Put incomplete validation and
the agent's verification status in the handoff, and deployment access requirements
in operational documentation. Missing documentation alone does not justify a
restriction, a generic unverified-applicability disclaimer, or a blocking question.
Do not infer universal validity either. Resolve material ambiguity or conflicting
evidence before finalizing dependent metadata. There is no minimum length or
mandatory number of paragraphs.

MCP catalog listings abbreviate descriptions, while individual metric inspection
returns the complete metadata. Put the summary first and retain necessary detail
for inspection. Before handoff, review the generated manifest and
`lyra-plugin describe` without the README and compare their explanations with the
workflow evidence. Check for missing essential context and remove details that do
not affect consumer decisions. The [agent-assisted authoring guide](../agent-assisted-authoring/)
explains how the skill applies this review.

## Handler conventions

Reserved argument names are `parameters`, `location`, `bounds`, and `context`.
Additional dependency arguments match the `inputs` mapping.
Their order does not matter: the worker calls them by keyword. Positional-or-keyword
and keyword-only arguments are accepted; positional-only arguments, variadics,
argument defaults, async handlers, and generators are rejected.

- `parameters` is a concrete Pydantic model. Omit this argument for a metric with
  no ordinary inputs. Put defaults on model fields, never on handler arguments.
- `location: LocationInput` receives a resolved `GeoJSON` feature collection of
  one or more Polygon/MultiPolygon features.
- `bounds: BoundsInput` receives a resolved `SingleGeoJSON` collection containing
  exactly one Polygon feature; it need not be rectangular.
- `context: RunContext` is optional. Declare it only when using platform services.

Metrics and datasets declare spatial arguments only when needed. Spatial arguments
cannot be nullable or have defaults. Lyra owns their schemas and descriptions. Return annotations are optional;
the explicit output declaration governs runtime validation.

Both inputs carry their declared CRS. GeoDataFrame conversion preserves it rather
than universally projecting to EPSG:4326. Keep reprojection required by the
calculation, and distinguish a region's size from a raster's reduction resolution.
Supported platform geometries do not establish dataset coverage or scientific
applicability at every scale.

Metric names must match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`.
Parameter fields use normal Pydantic names without aliases.

## Reuse existing models

An existing `BaseModel` subclass is accepted when the root and every nested model
set `extra="forbid"` and `validate_default=True`. Inherited settings count. Adapt
an incompatible model with a small subclass and, where necessary, compatible
nested field types. Lyra never mutates or clones a model to change its behavior.

Root parameter fields require descriptions. Nested descriptions and examples are
optional. Supported values are strings, booleans, integers, finite floats, null,
homogeneous scalar `Literal` enums, lists, string-keyed dictionaries, unions, and
nested models. Typed dictionaries are intentionally open mappings.

Use ordinary lists with fixed output columns. Independent parameter sweeps can be
separate jobs; a plugin may process a list internally and return its declared
fixed columns.

The parameter contract excludes `Any`, arbitrary classes, root/recursive/unresolved
generic models, sets, tuples, bytes, datetimes, decimals, non-string dictionary
keys, aliases, default factories, custom serializers, computed fields, and custom
JSON Schema hooks or structural overrides. Registration errors identify the metric
and field path. These limits keep discovery and execution contracts predictable.

## Defaults and validation

Omission and nullability are independent:

| Model field | May be omitted | Accepts null | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | — |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | — |
| `value: int \| None = None` | Yes | Yes | null |
| `value: int \| None = 1` | Yes | Yes | `1` |

Defaults must be deterministic JSON values. Mutable literal defaults follow
Pydantic's per-instance copying behavior. Manifest generation validates defaults
and examples against their schemas; dynamic default factories are unsupported.

The API checks the submitted JSON Schema without coercion or default injection.
The worker constructs the parameter model, applies defaults, and runs Python
validators. A numeric string is not an integer; a JSON number such as `1.0` may
satisfy an integer schema and is parsed using normal Pydantic JSON semantics.

Python-only validators can reject schema-valid requests. Prefer model-level after
validators for cross-field checks and preserve declared value types. Such a request
can be accepted, then fail in the worker with `invalid_input` before the handler
runs. An invalid semantic default has the same outcome. A successful schema check
does not promise successful execution of every domain rule.

Submitted input is retained in provenance. Omitting a defaulted field and explicitly
supplying its default remain distinct captured provenance.

A known Pydantic schema-generation limitation affects literal dictionary defaults
containing a `$ref` key. Generation may fail with `PluginDefinitionError`; ordinary
dictionary parameters remain supported. Lyra does not rewrite these defaults.

## Submit spatial references and nested parameters

For the example table metric:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"value": 7}
  }
}
```

A declared parameter model always requires the `parameters` object, including `{}`
when all fields have defaults. For a parameterless metric, omit the property:

```json
{
  "metric": "smoke_summary_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"}
  }
}
```

The API resolves spatial wrappers before execution. REST and Python clients support
both spatial arguments together. MCP discovers dual-spatial metrics but its run
helper supports exactly one spatial input; see the [MCP workflow](../../use/mcp/).

## Dataset graphs

Use `@dataset` for external acquisition and intermediate transformations and
`@metric` for public calculations using supplied tables. Register both with
`PluginDefinition(nodes=[...])`. Dataset-only plugins are supported. Metrics and
calculation helpers obtain domain data through declared dependencies. This is a
trusted-plugin authoring rule; Python filesystem and network operations are not
intercepted. HTTP clients, Earth Engine and file readers are ordinary plugin
implementation choices inside datasets. Raster acquisition and file processing
can happen inside a dataset; its graph outputs are tables. `context.temp_dir` is
for scratch work.

Node names identify calculations and public requests. Output names identify whole
tables, not columns. Both namespaces are globally unique across enabled plugins;
an output may share only its own producer's node name. For example:

```python
inputs = {
    "records": Dependency(
        "example_selected_records",
        arguments={"parameters": {"year": Input("parameters.baseline_year")}},
    )
}
```

The key `records` is a local function argument; the referenced name is an explicit
output, with no fallback to a node name. `Input` refers to a directly declared
field of the current node, not the root request. References can cross plugins
without importing the producer. Each node declares its own parameters; dependencies
do not add public request fields. Context and dependency arguments are excluded
from public request schemas.

Upstream ordinary fields resolve from explicit overrides, then matching effective
consumer parameters (including defaults), then producer defaults; otherwise
resolution fails. Explicit `None` is a value. Overrides replace whole fields,
including nested objects. `arguments` accepts only `parameters`; context, tables,
and spatial arguments are not bindings. Required `location` and `bounds` forward
unchanged by name and kind. Every consuming node must declare the spatial inputs
needed by its dependencies even when its calculation only uses their tables.

Fixed-region datasets own their region and sources. Their public metrics can omit
both spatial arguments; a boundary needed downstream can be a GeoDataFrame
dependency. Client-selected graphs declare spatial inputs throughout their
forwarding paths. Plugins own coverage checks, geometric transformations, joins,
identifiers, and density denominators. `calculate_feature_areas_m2` is available
from `lyra.utils.geometry` when appropriate to the plugin's calculation.

`RunContext` provides `job_id`, `node`, `logger`, `temp_dir`, and
`report_progress(...)`. Metrics receive no database client. Datasets can declare
`context: DatasetContext` and call:

```python
frame = context.db.query_dataframe(sql, params={"year": 2020})
spatial = context.db.query_geodataframe(
    sql, params={"year": 2020}, geom_col="geometry", crs="EPSG:6372"
)
```

Use bound SQL parameters. Source names and query logic belong to the plugin;
Lyra manages connections. Spatial queries normally infer CRS from returned SRIDs.
An explicit `crs` labels coordinates, without reprojecting them, and can supply
metadata for empty or all-null geometry results. Supply the correct source CRS.

Registration and manifest generation check local declarations and signatures.
They work without installing referenced implementations or accessing sources.
Loading enabled manifests resolves references and checks name collisions, cycles,
parameter paths and required spatial forwarding for the whole graph. Execution
validates actual arguments, basic return shapes and public serialization.

Direct callable invocations supply dependency tables explicitly; they do not
execute the graph. For local graph tests, build manifests with
`PluginDefinition.manifest(...)`, pass them to `lyra.sdk.graph.build_graph`, and
create `lyra.sdk.execution.GraphExecutor(graph, plugins, context_factory)`.
Execute `plugin.prepare_invocation(metric_name, resolved_input)` with fake source
clients. The plugins mapping uses the same distribution keys as the graph.

A requested metric returns all its declared outputs; intermediate dependency
outputs remain internal. It executes dependencies sequentially on the selected worker.
Identical producer invocations reuse native tables within that job, including all
outputs of a multi-output producer. Consumers treat dependencies as read-only and
make an appropriate explicit copy before mutation. Lyra neither copies tables
nor enforces immutability. Separate jobs acquire their inputs independently.
Failures stop execution and fail the requested job without partial results or
automatic retries. Inspect the failing node and dependency path in diagnostics
and worker logs.

## Native table ownership and encoding

Every node declares a nonempty `outputs` mapping. One output may return its native
DataFrame or GeoDataFrame directly; multiple outputs return a dictionary with
exactly the declared keys. `None` is not an output. Metadata describes attribute
columns, their types (`integer`, `number`, `string`, `boolean`, `date`, `datetime`),
units, descriptions and nullability, plus an optional geometry column. CRS belongs
to the returned GeoDataFrame. Metadata is descriptive; runtime checks enforce
native table types and output keys, not cell schemas, column order or row identity.

Producers own row counts, order, indices and identifiers. Publish identifiers as
ordinary columns. Do not assign request feature IDs to unrelated result rows.
Plugins calculate area fractions and derived columns explicitly. Consumers own
any joins, aggregation or alignment they require.

Zero-row tables succeed and still flow to consumers. Preserve their columns and,
for spatial outputs, active geometry and correct CRS. Return every declared table
even when empty. Public ordinary tables encode as record arrays (`[]` when empty),
without indices. Spatial tables encode as WGS84 GeoJSON FeatureCollections with
no index-derived feature IDs. Missing CRS fails serialization; conversion leaves
the native graph table unchanged. Missing cells encode as null, dates/timestamps
as ISO strings; unsupported values fail clearly. Empty spatial outputs have an
empty `features` array. Clients retrieve the complete JSON result and select a
named table with `client.result_tables(job_id)["output_name"]` or
`client.results.tables(job_id)["output_name"]`; async clients await those methods.

### Output units

Import `Unit` from `lyra.sdk` and declare, for example, `unit=Unit.SQUARE_METRE`.
Every column must explicitly supply `unit`: a `Unit` member, an exact canonical
string, or `None` when no unit applies. JSON uses canonical strings or `null`.
Labels, identifiers, and booleans normally use `None`; numeric identifiers can
also have no applicable unit. Omission, unknown values, aliases, and incorrect
casing are rejected.

| Enum member | Value | Meaning |
| --- | --- | --- |
| `MILLIMETRE` | `mm` | Millimetres of length. |
| `METRE` | `m` | Metres of length. |
| `KILOMETRE` | `km` | Kilometres of length. |
| `SQUARE_METRE` | `m2` | Square metres of area. |
| `SQUARE_KILOMETRE` | `km2` | Square kilometres of area. |
| `HECTARE` | `ha` | Hectares of area. |
| `DEGREE_CELSIUS` | `degC` | Temperature in degrees Celsius. |
| `KELVIN` | `K` | Temperature in kelvins. |
| `SECOND` | `s` | Duration in seconds. |
| `DAY` | `day` | Duration in days. |
| `YEAR` | `year` | Duration in years; document the workflow's year convention. |
| `CALENDAR_YEAR` | `calendar_year` | Calendar-year value, not an elapsed duration. |
| `COUNT` | `count` | Number of entities; describe what is counted. |
| `RATIO` | `ratio` | Quotient; describe its numerator and denominator. |
| `PERCENT` | `percent` | Value expressed per hundred (50 means 50%). |
| `SCORE` | `score` | Workflow-defined scale; describe its meaning and interpretation. |
| `DIMENSIONLESS` | `dimensionless` | Known unitless quantity without a more specific applicable unit. |

Units declare meaning; they do not convert values or impose numeric ranges or
column-type restrictions. A ratio is not automatically restricted to 0–1.
`None` means not applicable, never unknown. Establish the unit from the workflow
before selecting an identifier. Do not substitute dimensionless, score, or null
for missing scientific information. If the required unit is absent from this
vocabulary, ask the user about adding it to the SDK; do not invent an identifier
or silently convert the calculation. Progress-reporting units are separate
human-readable labels.

## Register and test locally

Register functions explicitly in a synchronous, parameterless factory:

```python
from lyra.sdk import PluginDefinition
from .metrics import run_table, run_summary, run_progress


def create_plugin() -> PluginDefinition:
    return PluginDefinition(nodes=[run_table, run_summary, run_progress])
```

Configure `[tool.lyra].factory = "smoke_plugin.plugin:create_plugin"` in
`pyproject.toml`. Declare directly imported dependencies there, including pandas
when returning DataFrames. The SDK imports pandas when checking native results.

Decorated functions remain ordinary Python callables. In a local test, construct
parameters and resolved geometry and pass a fake context only if the handler needs
one. Direct calls neither resolve spatial references nor inject context or normalize
results. Test your existing calculation independently as usual.

The same preparation and normalization used by workers are available locally:

```python
# In the example plugin environment, with resolved location and a fake context:
from smoke_plugin.metrics import run_table
from smoke_plugin.plugin import create_plugin

plugin = create_plugin()
parameters = plugin.prepare_parameters("smoke_table_metric", {"value": 7})
frame = run_table(parameters=parameters, location=location, context=context)
outputs = plugin.normalize_result("smoke_table_metric", frame)
assert outputs["smoke_table_metric"] is frame
```

`prepare_parameters` raises `MetricInputError` for invalid input. `normalize_result`
checks output names and native table types, returning named tables by reference.
Invalid results raise `MetricResultError` with node and output context. Neither helper
requires API, Redis, RQ, PostGIS, or Earth Engine connections.

## Runtime context

### Earth Engine

The normal worker launcher initializes Earth Engine before loading plugins.
Deployment settings `earth_engine.project` and `earth_engine.service_account_file`
provide the project and service-account credentials. Datasets use this initialized
environment; handlers and factories should not authenticate, initialize Earth
Engine, read application configuration, or expose credential/project parameters.
Private asset permissions remain a deployment requirement.

Importing a plugin and generating its manifest must work without Earth Engine
initialization. Import `ee` normally, but create initialization-dependent images,
collections, reducers, and expressions during dataset execution, not at module
scope, in function defaults, or in the factory.

Standalone scripts and direct SDK calls do not run worker initialization. Use
mocks for offline tests and a separate setup harness for authorized live tests.
The helper `lyra.utils.ee.reduce_ee_image_over_gdf` reprojects geometry to
EPSG:4326; its `scale` argument is reduction resolution in metres. Preserve the
workflow's existing dataset, reducer, and resolution rather than inventing new
defaults.

### Job services

`RunContext` supplies job and node identity, a logger, a job temporary directory,
and best-effort progress reporting. Dataset handlers can declare
`context: DatasetContext` for database access.

`context.report_progress(stage=..., current=..., total=..., unit=..., message=...)`
publishes optional snapshots. Current is finite and nonnegative; total, when supplied,
is finite and positive with current no greater than total. Estimates may decrease and
stages, totals, and units may change. Updates are coalesced using
`jobs.progress_min_interval_ms` (default 1000 ms). Persistence failures do not fail computation.
Plugins may remain silent for hours. Tests for database-using adapters should supply
a strict fake database client.

## Generate and inspect the manifest

Run in the plugin project:

```sh
uv run pytest
uv run lyra-plugin describe smoke_table_metric
uv run lyra-plugin describe smoke_table_metric --json
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
```

Commit the generated `lyra.plugin.json`; never edit it manually. It contains
plugin identity, the factory, node roles, dependencies, complete Draft 2020-12
request schemas, spatial metadata, and named output declarations. Generation reads project
metadata and the live factory.

`describe` reads the canonical contract. `check-manifest` rejects a stale artifact.
The API reads manifests without importing plugin code; worker startup checks that
the live definition matches. See [Publish and debug](../publish-and-debug/) for
packaging and deployment.
