---
title: Define metric inputs
description: Add typed parameters and spatial arguments to a synchronous calculation.
contentType: How-to
contentPlan: plugins/authoring
sidebar: { label: "Define Metric Inputs" }
---

Define a [metric’s](../../concepts/model/#a-metric-declares-a-calculation) ordinary parameters and spatial inputs so callers know what to supply. Put ordinary choices in a Pydantic parameter model and declare spatial arguments on the function. Lyra derives the request schema from those annotations.

Keep the underlying calculation in its own function or library when practical. The Lyra adapter can translate inputs and pass them to that code.

## Choose the input roles

Declare ordinary choices through a parameter model and named spatial inputs through `Locations` arguments. [The city tutorial](../tutorial/parameters/) builds and runs a complete adapter with both. This guide describes choices you can make while adapting your own function.

## Add parameters and defaults

Inherit from `MetricParameters` and use Pydantic `Field` declarations for descriptions, constraints, defaults, and examples. For example:

```python {6-10}
from pydantic import Field
from lyra.sdk import MetricParameters


class Parameters(MetricParameters):
    value: int = Field(
        default=7,
        ge=0,
        description="Nonnegative value copied into each output row.",
        examples=[7, 12],
    )
```

Put defaults on model fields. Handler arguments themselves cannot have defaults. Omit the `parameters` argument entirely if the calculation has no ordinary inputs.

Include `parameters` when required fields need values. You may omit it when an empty object satisfies the parameter schema; preparation applies declared defaults.

## Distinguish an omitted field from null

Whether a field may be omitted is independent of whether it accepts null. Use the declaration that matches the choices your calculation supports:

| Declaration | May be omitted? | Accepts null? | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | No default |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | No default |
| `value: int \| None = None` | Yes | Yes | null |
| `value: int \| None = 1` | Yes | Yes | `1` |

Use `Field(default_factory=...)` for containers and nested parameter models. Pydantic validates defaults and copies mutable literal defaults for each model instance. Use `JsonValue` for JSON values containing nested lists and objects.

Write factories and validators that follow these rules:

- Return the same values for the same inputs.
- Do not perform external input/output (I/O), such as accessing files or services.
- Keep normalized values unchanged when validators run again, including after JSON serialization and parsing.

Lyra prepares parameters for every selected metric or operation invocation before acquiring data or queueing a job. Each invocation’s Pydantic model applies defaults and validators. Lyra retains every resulting value, including nulls, and uses JSON Schema to describe parameters for discovery.

Pydantic converts compatible input types; for example, an ordinary integer field accepts the string `"7"`. Use strict Pydantic fields when inputs must match their declared types.

Use model-level after validators for cross-field checks and preserve declared types. Lyra rejects invalid parameters during preparation, before any handler runs. Request [provenance](../../concepts/provenance/) records the resulting parameter values used for execution and [cache identity](../../concepts/caching/).

## Declare spatial inputs

Declare zero or more named `Locations` arguments for feature collections of Point, MultiPoint, LineString, MultiLineString, Polygon, or MultiPolygon features.

Describe each location argument with `Annotated[Locations, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties.

Lyra stores each description with its location input declaration. Generated request schemas and inspection results include these descriptions.

Use prose to explain scientific requirements; the declared concrete location contracts and schemas enforce structural and source compatibility.

A `Locations` value stores an immutable snapshot. Exported features, geometries, properties, and payloads are independent copies. Construct a new value when you need different contents.

Declare only the location inputs the metric and its dependencies need. Each declared location argument is required and cannot accept null or have a default. Lyra supplies the structural schema.

The handler receives a `Locations` value containing its scope (`collection` and `unit_type`), ordered `ids`, features, and CRS.

Use `location.to_geodataframe()` to create an independent GeoDataFrame with IDs in the declared ID column. If the ID column is `id`, append `.set_index("id")` to use feature IDs as the index. Conversion preserves feature order and the declared CRS; reproject explicitly when your calculation requires it.

Frame exports preserve declared data types, including columns with no rows or only nulls, and use a fresh `RangeIndex`. Every row has every declared property. Lyra treats missing values as nulls and permits them only in nullable columns. Conversion rejects geometry dimensions or structures that Shapely cannot preserve.

Location inputs contribute their concrete contract, data revision, ordered features, properties, and CRS to invocation and cache identity. See [Aggregate selected lots by block](../location-example/) for two named inputs and an explicit dependency binding. Use [location operations](../location-operations/) to derive inputs internally or construct locations from table dependencies.

### Declare concrete location contracts

Pass `locations={"subject": contract}` to `metric` or `location_operation` for each named `Locations` argument. Reuse the contracts registered by the [location provider](../location-providers/). Lyra checks each input’s concrete source and geographical edition identity; matching columns or geographic IDs do not make sources interchangeable.

For an operation, pass `output_contract=target_contract` alongside its `output` schema. The target contract defines the collection and unit type; its family structure must match the output schema. Lyra assigns that concrete identity to operation results and checks cached results against it. Operation handlers receive `Locations` objects and return GeoDataFrames.

For a named input with several reachable contracts sharing a source scope, set `source_bindings={input_name: (source_contract.reference,)}` on the metric. Each binding must be reachable and unique per source collection/unit type, and cannot replace the native contract for direct native selections. Unambiguous sources need no override.

## Describe output meaning and granularity

Use Pandera schema metadata to describe what the metric returns and what each row represents. Lyra preserves `meaning` and `granularity` alongside column definitions and units in generated node metadata.

For example, pass this metadata to `pa.DataFrameSchema` for a metric that returns one population value per tract:

```python
metadata = {
    "meaning": "Resident population",
    "granularity": "One row per tract",
}
```

These declarations describe the calculation independently of source selection.

## Reuse an existing parameter model

An existing `BaseModel` works when it and every nested model set `extra="forbid"` and `validate_default=True`. Inherited settings count. Adapt an incompatible model with a small subclass and compatible nested types; Lyra leaves the original model unchanged.

See [parameter and handler rules](../../reference/parameters/) for the supported types and signature constraints. Continue to [output tables](../outputs/) to describe the result and [testing](../testing/) to exercise the adapter.

## Define concrete metrics

Register separately selectable calculations with unique metric names. Follow [Register concrete metrics](../metrics/) for factories and dependency bindings.

## Assign an implementation revision

Declare `code_revision="1"` on each `@metric` and `@location_operation`. Together, the registered `name` and your `code_revision` identify the implementation.

Choose a revision that meets these rules:

- Use 1 to 256 characters.
- Start with a letter from `A` to `Z` or `a` to `z`, or a digit.
- Use only those letters, digits, and `._:+-` afterward.

Change your revision when you change computation behavior, including behavior from helpers or dependencies. Package versions and [deployment build revisions](../../operate/configuration/#assign-a-deployment-build-revision) do not replace this declaration.

Lyra also uses these values to identify a computation for caching:

- Effective parameters, including defaults and normalized values
- Declared semantics and dependencies
- Current input and data scopes

Use `cache_revision` on a request to select a [cache namespace](../../operate/caching/).

## Make intermediate metrics private

Metrics are public by default. Set `private=True` on `@metric` to exclude a metric from discovery and direct public execution, including `LocalRunner.run`. The `private` flag applies only to metrics.

Public metrics can depend on private metrics, and Lyra executes and caches those dependencies normally. Private metrics remain visible in dependency inspection; `PluginDefinition.describe` includes their `private` flag.

To test a private metric through the graph, call `runner.run_development()` with its name, `inputs=`, and `parameters=`. Use the same [named ID selections](../../use/local-execution/#supply-id-selections) as public calls. You can also call decorated Python functions directly for tests.

## Inspect node declarations

Call `PluginDefinition.describe(name)` to inspect a node before publishing your plugin. The returned `NodeDescription` includes the function’s name and signature. Its `manifest` field contains an independent description of the node.

Read `description.manifest.request_schema` for the complete generated input schema. The command-line interface (CLI) includes this schema in its JSON inspection output. See [Package a plugin](../publish-and-debug/) for inspection commands.

Inspect these fields on `description.manifest`:

| Field | What to inspect |
| --- | --- |
| `parameters_schema` | The ordinary parameter schema; `null` in JSON means the handler has no parameter argument |
| `location_inputs[name].contract` | The named input’s native location contract |
| `location_inputs[name].description` | The optional argument description |
| `location_inputs[name].source_bindings` | The source overrides for that input |

Check `manifest.kind` to choose the output declaration: metrics expose table `outputs`, while operations expose `output_contract`. An operation’s `output_contract.family` defines structure and scope; its executable `output` schema supplies coercion and custom validation. Lyra checks that their structures agree during registration.

## Inspect registered implementations

Runtime code can look up a metric or location operation with
`plugin.implementation(name)`. The returned `lyra.sdk.NodeImplementation`
exposes the original handler as `function`, its parameter model as `parameters`
(or `None`), and its node `manifest`. The `accepts_context` and
`requires_resolver` flags describe the context to supply. Each entry in
`manifest.resources` maps a handler argument to its registered resource name.
An unknown name raises `PluginDefinitionError` and lists the available nodes.

For metrics, `schemas` maps output names to compiled Pandera schemas. For
location operations, `location_schema` supplies the executable location contract.
Descriptor fields are frozen. Each lookup returns independent manifest metadata,
a fresh schema mapping, and a detached location schema wrapper when applicable.
Treat the shared callable, model class and compiled Pandera schemas as read-only.

Use `plugin.resources` to look up registered resource classes by name and
`plugin.location_provider` to inspect the designated provider’s declaration.
Both are available before resource acquisition.
