---
title: Define metric inputs
description: Add typed parameters and spatial arguments to a synchronous calculation.
contentType: How-to
contentPlan: plugins/authoring
sidebar: { label: "Define Metric Inputs" }
---

A metric exposes the inputs a caller can supply. Put ordinary choices in a Pydantic parameter model and declare spatial arguments on the function. Lyra derives the request schema from those annotations.

Keep the underlying calculation in its own function or library when practical. The Lyra adapter can translate inputs and pass them to that code.

## Start with a complete adapter

Build the adapter in three parts in a file named `metric.py`. Start with its imports and parameter model; the completed function copies an integer into one row per input feature:

```python title="metric.py"
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.sdk import (
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

Finally, append the handler and its decorator, connecting the parameter model and output declaration to the calculation:

```python title="metric.py" {4}
@metric(
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
    feature_ids = [feature.id for feature in location.features]
    return pd.DataFrame(
        {"value": [parameters.value for _ in feature_ids]},
        index=feature_ids,
    )
```

The decorator infers `Parameters` from the function annotation. Its fields describe the choices callers can make. The function remains directly callable for unit tests.

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

A server request must include `parameters` whenever the metric has a parameter model. An empty object is enough if all its fields have defaults.

## Distinguish an omitted field from null

Whether a field may be omitted is independent of whether it accepts null. Use the declaration that matches the choices your calculation supports:

| Declaration | May be omitted? | Accepts null? | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | No default |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | No default |
| `value: int \| None = None` | Yes | Yes | null |
| `value: int \| None = 1` | Yes | Yes | `1` |

Defaults must be deterministic JSON values. Pydantic copies mutable literal defaults for each model instance. Manifest generation checks defaults and examples against their schemas.

The API checks JSON Schema without applying defaults or coercing values. The worker constructs the parameter model, applies defaults, and runs Python validators. A numeric string does not satisfy an integer schema. A JSON number such as `1.0` can satisfy it; Pydantic parses it using JSON semantics.

Use model-level after validators for cross-field checks and preserve declared types. A Python-only rule can reject an accepted job with `invalid_input` before the handler runs. Request provenance records the submitted values, including whether you omitted a field that has a default.

## Declare spatial inputs

Declare zero or more named `Locations` arguments for feature collections of Point, MultiPoint, LineString, MultiLineString, Polygon, or MultiPolygon features.

Describe each location argument with `Annotated[Locations, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties. Lyra includes these descriptions in inspection results and manifest request schemas without enforcing compatibility rules.

Treat location values as read-only because multiple nodes may share them. Copy features before changing them.

Declare only the location inputs the metric and its dependencies need. Each declared location argument is required and cannot accept null or have a default. Lyra supplies the structural schema.

The handler receives a `Locations` value containing its scope (`collection` and `unit_type`), ordered `ids`, features, and coordinate reference system (CRS).

Use `location.to_geodataframe()` to create an independent GeoDataFrame with IDs in an `id` column. Append `.set_index("id")` if your calculation expects feature IDs as the index. Conversion preserves feature order and the declared CRS; reproject explicitly when your calculation requires it.

Location inputs contribute their collection, unit type, features, properties, and CRS to invocation and cache identity. See [Aggregate selected lots by block](../location-example/) for two named inputs and an explicit dependency binding. Use [location operations](../location-operations/) to derive inputs internally or construct locations from table dependencies.

## Reuse an existing parameter model

An existing `BaseModel` works when it and every nested model set `extra="forbid"` and `validate_default=True`. Inherited settings count. Adapt an incompatible model with a small subclass and compatible nested types; Lyra leaves the original model unchanged.

See [parameter and handler rules](../../reference/parameters/) for the supported types and signature constraints. Continue to [output tables](../outputs/) to describe the result and [testing](../testing/) to exercise the adapter.
