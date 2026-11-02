---
title: Define metric inputs
description: Add typed parameters and spatial arguments to a synchronous calculation.
contentType: How-to
contentPlan: plugins/authoring
sidebar: { label: "Define Metric Inputs" }
---

Define a [metric’s](../../concepts/model/#a-metric-declares-a-calculation) ordinary parameters and spatial inputs so callers know what to supply. Put ordinary choices in a Pydantic parameter model and declare spatial arguments on the function. Lyra derives the request schema from those annotations.

Keep the underlying calculation in its own function or library when practical. The Lyra adapter can translate inputs and pass them to that code.

## Start with a complete adapter

Build the adapter in `metric.py`. Start with its imports and parameter model; the completed function copies an integer into one row per input feature:

```python title="metric.py"
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.sdk import (
    LocationContract,
    LocationFamily,
    LocationOutputSchema,
    Locations,
    MetricParameters,
    RunContext,
    Unit,
    metric,
)


class Parameters(MetricParameters):
    value: int = Field(description="Value copied into each output row.")
```

The parameter model describes the integer that callers supply. Append an output declaration to the same `metric.py` file so the catalog also explains the returned values:

```python title="metric.py" {6}
value_output = pa.DataFrameSchema(
    {
        "value": pa.Column(
            "int64",
            description="Submitted value.",
            metadata={"unit": Unit.COUNT},
        )
    }
)
```

Declare the input location schema. This example requires only an ID and geometry:

```python title="metric.py"
location_schema = LocationOutputSchema(
    pa.DataFrameSchema(
        {
            "id": pa.Column(str, description="Unit ID", metadata={"unit": None}),
            "geometry": pa.Column(
                "geometry", description="Unit geometry", metadata={"unit": None}
            ),
        }
    )
)
```

Bind the schema to the example’s source and geography. For provider-backed inputs, reuse the provider’s contract:

```python title="metric.py"
LOCATION_CONTRACT = LocationContract(
    family=LocationFamily.from_schema(
        "test:units",
        "lot",
        location_schema,
    ),
    source="fixture",
    geographical_edition="current",
    meaning="Provider locations",
)
```

Append the handler and its decorator to connect the input contracts and output declaration to the calculation:

```python title="metric.py" {2}
@metric(
    locations={"location": LOCATION_CONTRACT},
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    outputs={"smoke_table_metric": value_output},
)
def run_table(
    location: Locations,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    context.report_progress(stage="table", current=1, total=1)
    return pd.DataFrame(
        {"value": pd.Series([parameters.value] * len(location), dtype="int64")},
    )
```

The decorator infers `Parameters` from the function annotation. Its fields describe the choices callers can make. The function remains directly callable for unit tests.

The explicit `int64` dtype keeps the output valid when the input location set is empty. Declare identifiers as columns when consumers need to align rows; Lyra resets producer indexes before validation. See [output declarations](../outputs/) for column types and validation rules.

## Add parameters and defaults

Inherit from `MetricParameters` and use Pydantic `Field` declarations for descriptions, constraints, defaults, and examples. For example:

```python
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

Shared request preparation validates JSON Schema and captures effective parameters with declared defaults before acquisition or enqueueing. It does not coerce values. During execution, the parameter model runs Python validators.

A numeric string does not satisfy an integer schema. A JSON number such as `1.0` can satisfy it; Pydantic parses it using JSON semantics.

Use model-level after validators for cross-field checks and preserve declared types. A Python-only rule can reject an accepted job with `invalid_input` before the handler runs. Request provenance records the submitted values, including whether you omitted a field that has a default.

## Declare spatial inputs

Declare zero or more named `Locations` arguments for feature collections of Point, MultiPoint, LineString, MultiLineString, Polygon, or MultiPolygon features.

Describe each location argument with `Annotated[Locations, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties. Lyra includes these descriptions in inspection results and manifest request schemas.

Use prose to explain scientific requirements; the declared concrete location contracts and schemas enforce structural and source compatibility.

Location values own immutable snapshots. Feature lists, geometries, properties, and serialized payloads are independent exports. Construct a new `Locations` value to apply changed contents.

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

Use editions to register variants of a metric under one `family` identifier. Create decorated handlers with ordinary Python factories, giving each handler a distinct `name` and each variant a named `edition`. Register every generated handler in `PluginDefinition`.

Bind dependencies to a concrete producer and its local output name. Factories can reuse output names without connecting their graphs; share a dependency by referencing the same producer and output. Declare column meanings and units in each output schema, and use `family` to associate metrics even when their schemas match.

Variants in a family must share these contracts:

- Named native input roles and location families
- Named output schemas
- The complete ordinary parameter contract, including names, types, defaults, and validation constraints

Parameter compatibility includes requiredness, nullability, bounds, and allowed values. Compilation rejects incompatible variants. Use separate families when their public parameter contracts differ.

A location family defines one structural schema for all its source contracts. It fixes column types, nullability, validation constraints, units, and ID and geometry conventions. Sources can differ in their values, boundaries, available IDs, and field definitions. A shared schema establishes neither geographic correspondence nor statistical comparability.

Use the `edition_metadata` JSON object to describe the data and methodology for each edition. Record these details as metadata:

- Source releases
- Observation periods
- Fixed methodological constants and their meanings

For development execution, use `plugin.editions.resolve(family, edition="your_edition_here")` to obtain the concrete node name. The compiled graph exposes the same resolver across enabled plugins.

Select a named edition even when you have installed only one; Lyra does not select the latest edition automatically. The resolver rejects unknown selections, and registration rejects duplicate editions within a family.

Omit `edition` for a metric with one implicit variant that requires no edition selector. Its family identifier defaults to its node name. Edition selection binds concrete sources and geographies separately from ordinary handler parameters. Do not repeat the edition inside each ID selection.

The [edition factory example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/capabilities.py) uses `score_edition` to register public `street_rating` variants and private producers. Those variants share schemas and calculation code; `edition_metadata` explains each variant’s fixed multiplier. [Run the example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) for multiple named inputs, native returns, and separate provenance inspection.

## Make intermediate metrics private

Metrics are public by default. Set `private=True` on `@metric` to exclude a variant from discovery and direct public execution, including `LocalRunner.run`. The `private` flag applies only to metrics.

Visibility applies to each variant, so a family can contain both public and private editions. Discovery omits families with no public variants.

Public metrics can depend on private metrics, and Lyra executes and caches those dependencies normally. Manifests and `PluginDefinition.describe` include the `private` flag for inspection. You can call decorated Python functions directly for development and tests.

Privacy controls public entry points, not secrecy. Private nodes remain visible in relevant dependency inspection. Use `runner.run_development(concrete_metric, inputs)` with resolved locations to exercise them through the graph without publishing them.
