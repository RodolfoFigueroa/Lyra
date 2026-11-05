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

Defaults must be deterministic JavaScript Object Notation (JSON) values. Pydantic copies mutable literal defaults for each model instance. Manifest generation checks defaults and examples against their schemas.

Before acquiring data or queueing a job, Lyra validates the request against JSON Schema and applies parameter defaults. This step does not coerce values. During execution, the parameter model runs Python validators.

A numeric string does not satisfy an integer schema. A JSON number such as `1.0` can satisfy it; Pydantic parses it using JSON semantics.

Use model-level after validators for cross-field checks and preserve declared types. A Python-only rule can reject an accepted job with `invalid_input` before the handler runs. Request provenance records the submitted values, including whether you omitted a field that has a default.

## Declare spatial inputs

Declare zero or more named `Locations` arguments for feature collections of Point, MultiPoint, LineString, MultiLineString, Polygon, or MultiPolygon features.

Describe each location argument with `Annotated[Locations, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties.

Lyra stores each description with its location input declaration. Generated request schemas and inspection results include these descriptions.

Use prose to explain scientific requirements; the declared concrete location contracts and schemas enforce structural and source compatibility.

A `Locations` value stores an immutable snapshot. Exported features, geometries, properties, and payloads are independent copies. Construct a new value when you need different contents.

Declare only the location inputs the metric and its dependencies need. Each declared location argument is required and cannot accept null or have a default. Lyra supplies the structural schema.

The handler receives a `Locations` value containing its scope (`collection` and `unit_type`), ordered `ids`, features, and coordinate reference system (CRS).

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

## Define metric editions

Use named editions for separately selectable source or methodological variants. Follow [Register metric editions](../editions/) for factories, family compatibility, and concrete dependency bindings.

## Make intermediate metrics private

Metrics are public by default. Set `private=True` on `@metric` to exclude a variant from discovery and direct public execution, including `LocalRunner.run`. The `private` flag applies only to metrics.

Visibility applies to each variant, so a family can contain both public and private editions. Discovery omits families with no public variants.

Public metrics can depend on private metrics, and Lyra executes and caches those dependencies normally. Manifests and `PluginDefinition.describe` include the `private` flag for inspection. You can call decorated Python functions directly for development and tests.

Privacy controls public entry points, not secrecy. Private nodes remain visible in relevant dependency inspection. Use `runner.run_development(concrete_metric, inputs)` with resolved locations to exercise them through the graph without publishing them.

## Inspect node declarations

Call `PluginDefinition.describe(name)` to inspect a node before publishing your plugin. The returned `NodeDescription` contains an independent manifest copy and the registered function’s name and signature.

Read `description.manifest.request_schema` for the complete generated input schema. The command-line interface (CLI) includes this schema in its JSON inspection output. See [Package a plugin](../publish-and-debug/) for inspection commands.

Inspect these fields on `description.manifest`:

| Field | What to inspect |
| --- | --- |
| `parameters_schema` | The ordinary parameter schema; `null` in JSON means the handler has no parameter argument |
| `location_inputs[name].contract` | The named input’s native location contract |
| `location_inputs[name].description` | The optional argument description |
| `location_inputs[name].source_bindings` | The source overrides for that input |

Check `manifest.kind` to choose the output declaration: metrics expose table `outputs`, while operations expose `output_contract`. An operation’s `output_contract.family` defines structure and scope; its executable `output` schema supplies coercion and custom validation. Lyra checks that their structures agree during registration.
