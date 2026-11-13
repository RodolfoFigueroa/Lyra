---
title: Declare output tables and units
description: Validate named DataFrames and GeoDataFrames with Pandera contracts.
contentType: How-to
contentPlan: plugins/outputs
sidebar: { label: "Declare Output Tables And Units" }
---

Every metric declares a nonempty `outputs` mapping from output names to Pandera models or schema objects. Lyra compiles independent snapshots during registration and validates fresh and cache-restored outputs before their consumers run.

## Declare a model

Use a Pandera `DataFrameModel` to name each column and define its validation rules. This model requires unique area codes and coverage fractions between zero and one:

```python {9,14}
import pandera.pandas as pa
from pandera.typing import Series
from lyra.contracts.units import Unit
from lyra.sdk import metric


class Coverage(pa.DataFrameModel):
    class Config:
        strict = True

    area_code: Series[str] = pa.Field(
        description="Source area identifier.",
        metadata={"unit": None},
        unique=True,
    )
    coverage: Series[float] = pa.Field(
        description="Fraction of the source area covered.",
        metadata={"unit": Unit.DIMENSIONLESS},
        ge=0,
        le=1,
    )
```

Pass the model to `@metric` through the output mapping:

```python {5}
@metric(
    code_revision="1",
    name="coverage",
    description="Coverage by source area.",
    outputs={"coverage": Coverage},
)
def coverage(): ...
```

The equivalent object declaration is:

```python {7,13}
coverage_schema = pa.DataFrameSchema(
    {
        "area_code": pa.Column(
            str,
            unique=True,
            description="Source area identifier.",
            metadata={"unit": None},
        ),
        "coverage": pa.Column(
            float,
            checks=[pa.Check.ge(0), pa.Check.le(1)],
            description="Fraction of the source area covered.",
            metadata={"unit": Unit.DIMENSIONLESS},
        ),
    },
    strict=True,
)
```

For each column, declare:

- A fixed, unique string name
- An explicit data type (`dtype`)
- A nonblank description
- A unit in `metadata["unit"]`, including `None` when no unit applies

Declare `strict=True` on schema objects or the effective model `Config`, including inherited configuration. Lyra rejects schemas that use:

- Optional columns
- Automatic insertion of missing columns
- Column names defined by regular expressions

Return exactly the declared columns in any order. Address columns by name.

Choose a supported dtype:

- Signed 64-bit integers and finite 64-bit floats, including pandas nullable equivalents
- Booleans and strings
- Geometry with declared nullability, geometry checks, and CRS

Lyra rejects unrestricted object, complex, nested, and custom extension dtypes. Express temporal and categorical values through supported strings or numbers.

## Configure coercion, checks, and indices

Lyra leaves dtype coercion disabled by default. Request it explicitly on a schema or column:

```python {6,8}
observations = pa.DataFrameSchema(
    {
        "count": pa.Column(
            "Int64",
            nullable=True,
            coerce=True,
            description="Observed count.",
            metadata={"unit": Unit.COUNT},
        ),
    },
    strict=True,
)
```

Use `nullable=True` to allow missing values. Empty tables must retain their declared columns and dtypes. Lyra rejects infinities.

Construct nullable integers with pandas’ `Int64` dtype. These frames both satisfy the `observations` declaration:

```python {3-4}
import pandas as pd

all_null = pd.DataFrame({"count": pd.Series([None], dtype="Int64")})
empty = pd.DataFrame({"count": pd.Series([], dtype="Int64")})
```

Lyra copies producer frames before validation and rejects explicit index declarations. Declare identifiers and uniqueness constraints, including compound keys, in columns because indexes and row positions don’t establish identity. Sort results explicitly for presentation.

Use built-in constraints, uniqueness rules, and named deterministic Python checks that operate on the supplied data:

```python
positive_total = pa.Check(
    lambda frame: frame["count"].sum() >= 0,
    name="positive_total",
    description="Total count is nonnegative.",
)
```

DataFrame checks can enforce relationships between columns. Lyra rejects checks that only warn, parsers, column filtering, missing-column insertion, and row dropping. Execution always validates the full schema and data, regardless of global Pandera settings.

To inspect multiple failed checks together, create a `BoundPlugin` as shown in the [direct adapter test](../testing/#test-the-adapter-directly). Call `binding.normalize_result(metric_name, frame, lazy=True)` to validate the frame. If validation raises `MetricResultError`, inspect its `failures` list.

## Declare spatial outputs

Declare exactly one column named `geometry` and return a GeoDataFrame with that active geometry and a known CRS:

```python {6}
from pandera.engines.geopandas_engine import Geometry

places = pa.DataFrameSchema(
    {
        "geometry": pa.Column(
            Geometry(crs="EPSG:4326"),
            checks=pa.Check(
                lambda s: s.is_valid & s.geom_type.eq("Point"), name="valid_points"
            ),
            nullable=True,
            description="Source location.",
            metadata={"unit": None},
        ),
    },
    strict=True,
)
```

Declare the expected CRS explicitly. Reproject producer frames before returning them; validation preserves their CRS. Empty spatial tables still need active geometry and CRS metadata.

## Describe units and inspect metadata

Units are descriptive metadata. Supply an existing `Unit` member, its canonical value, or `None`. Units do not convert numbers or enforce ranges. See the [unit vocabulary](../../reference/units/).

Lyra generates inspection and API metadata from the registered schema snapshot. These descriptions include column constraints, units, and geometry.

Built-in check parameters use JSON values. Metadata describes Python checks by name and description; it cannot reconstruct their executable validation. Lyra compiles executable catalogs from registered live definitions. Keep imports and inspection free of data acquisition or resource initialization.

## Return and encode validated tables

Return one DataFrame directly for one output, or a dictionary with exactly the declared names for several outputs. Include empty tables: every declared output must validate before an invocation succeeds.

Repeated graph references reuse the validated result. Lyra supplies an independent frame for each handler argument, so your edits cannot affect another consumer or the retained result.

Tables passed between metrics retain declared data types, including columns with no rows or only nulls. Arrow IPC cache artifacts preserve those types too.

Check any consumer assumptions beyond the producer contract, such as required properties or supported geometry. [Graph execution errors](../../concepts/graphs/#stop-at-the-first-failure) identify the failing node and dependency path.

Use declared location revisions for covered dataset changes. Use `cache_revision` for changes outside that coverage or for unversioned implementation changes. Output validation checks the current schema; it cannot detect either change. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions).

Schema validity and serialization support are distinct requirements. Ordinary tables become records; spatial tables become GeoJSON in World Geodetic System 1984 (WGS84). Missing values become null.

Publish identifiers as columns and align dependencies using those columns. Public decoding does not restore native pandas dtypes.

For isolated plugin tests, use `schema.strategy()` or `Model.to_schema().strategy()` with Hypothesis. Custom checks and geometry may need explicit strategies that generate valid data. Test invalid examples and public encoding as well as schema-generated valid examples. See [plugin tests](../testing/) and [result handling](../../use/results/).

## Declare location ownership

Mark one `LocationDependency` as `primary=True` to assign output rows to that input’s locations. Every output then requires non-null string `location_id` and `row_key` columns. The primary input’s [provider scope](../location-providers/#define-revision-coverage) identifies the owning dataset and revision.

Declare `unique=["location_id", "row_key"]` on each output schema. Choose keys that identify the calculation’s rows consistently, regardless of row order. For example, use `row_key="area"` when each location owns one area measurement.

Without a primary input, the metric is unpartitioned: it calculates over the complete selection without assigning output rows to locations. Use this scope for summaries, including summaries that consume location-scoped metric outputs.

### Handle shared targets and references

[Operation mappings](../location-operations/) can associate several sources with the same target. Lyra forwards every source/target pair to the metric. Choose the rows your calculation needs:

- Deduplicate `location_id` when calculating once per target record
- Keep every pair and use distinct stable keys when calculating relationships
- Keep independent reference selections complete, even when their IDs overlap the primary selection

Unpartitioned dependencies also retain their complete input context. Result collections are unordered, so sort them explicitly for presentation.

You’re responsible for deterministic results and semantic correctness. Declare all dependencies and origins, choose stable keys, and honor any independence promise you make. Structural validation cannot prove those properties. For resource sharing across acquisition and calculation, follow [resource lifecycle rules](../resources/#manage-resource-clients-and-credentials).
