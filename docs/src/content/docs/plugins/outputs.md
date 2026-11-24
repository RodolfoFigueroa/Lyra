---
title: Declare output tables and units
description: Validate named DataFrames and GeoDataFrames with Pandera contracts.
contentType: How-to
contentPlan: plugins/outputs
sidebar: { label: "Declare Output Tables And Units" }
---

Declare each output table in a nonempty `outputs` mapping using Pandera models or schema objects. Lyra compiles these declarations for validation and storage when you register the metric. It validates fresh outputs before consumers or storage receive them.

## Declare a model

Use a Pandera `DataFrameModel` to name each column and define its validation rules. This model requires unique area codes and coverage fractions between zero and one:

```python {12,17}
import pandera.pandas as pa
from pandera.typing import Series
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
        metadata={"unit": "dimensionless"},
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
            metadata={"unit": "dimensionless"},
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

## Validate typed columns and row identities

Each named output must declare at least one column. An unpartitioned metric can return a geometry-only table. Convert source data and fill any required values before returning the frame. For example, declare a nullable integer count as follows:

```python {5,6}
observations = pa.DataFrameSchema(
    {
        "count": pa.Column(
            "Int64",
            nullable=True,
            description="Observed count.",
            metadata={"unit": "count"},
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

Use built-in constraints, uniqueness rules, and named, read-only, deterministic Python checks that operate on the supplied data:

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

Set `metadata["unit"]` on each column. In the coverage example, `area_code` uses `None` because it identifies an area. The coverage fraction uses `"dimensionless"` because it divides one area by another. For measurements such as length or temperature, see [unit expressions in Python and JSON](../../reference/units/).

Lyra generates inspection and API metadata from the registered schema description. These descriptions include column constraints, units, and geometry.

Built-in check parameters use JSON values. Metadata describes Python checks by name and description; it cannot reconstruct their executable validation. Lyra compiles executable catalogs from registered live definitions. Keep imports and inspection free of data acquisition or resource initialization.

## Return and encode validated tables

Return one DataFrame directly for one output, or a dictionary with exactly the declared names for several outputs. Include empty tables: every declared output must validate before an invocation succeeds.

Repeated graph references reuse the validated result. Lyra supplies an independent frame for each handler argument, so your edits cannot affect another consumer or the retained result.

Use the same Pandera declaration for validation and SQLite storage; you don’t need a second database schema. Registration rejects unsupported types without opening a database. Storage reconstructs these logical types:

| Logical column | Storage and native reconstruction |
| --- | --- |
| String | `TEXT`; pandas string |
| Signed 64-bit integer | `INTEGER`, from `-2**63` to `2**63 - 1`; pandas nullable `Int64` |
| Finite 64-bit float | `REAL`; pandas nullable `Float64` |
| Boolean | SQLite stores `0` or `1` as `INTEGER`. Lyra reads the column with pandas’ nullable `boolean` dtype. |
| Geometry | WKB in a `BLOB`; Shapely/GeoPandas with the declared CRS |

Lyra stores outputs in [SQLite strict tables](https://www.sqlite.org/stricttables.html), which require SQLite 3.37 or newer. If your declaration allows missing values, SQLite stores them as `NULL`.

When you read an output, Lyra uses the declared type for each column, including empty and all-null columns. Lyra preserves logical values even if the dtype width or backing array changes.

You own returned frames and nested metadata; editing them doesn’t change retained values. Address columns by name and rows by declared identifiers. Sort explicitly when you need a presentation order.

Check any consumer assumptions beyond the producer contract, such as required properties or supported geometry. [Graph execution errors](../../concepts/graphs/#stop-at-the-first-failure) identify the failing node and dependency path.

With `cache="per_location"`, the handler may receive only locations with cache misses. That batch must pass every check independently, and combining its rows with retained partitions must also satisfy the checks. Use `cache="full"` for constraints such as uniqueness of a code across locations.

Change the metric’s `code_revision` when a validation change can reject previously accepted output. Update origin or provider identities when datasets change. Change the node ID or `code_revision` when declarations or callbacks change outputs. Your calculation must remain deterministic even with caching off; validation can’t prove that property. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions).

Schema validity and serialization support are distinct requirements. Ordinary tables become records; spatial tables become GeoJSON in WGS84. Missing values become null.

Publish identifiers as columns and align dependencies using those columns. Python result readers reconstruct logical dtypes from saved output descriptions.

For isolated plugin tests, use `schema.strategy()` or `Model.to_schema().strategy()` with Hypothesis. Custom checks and geometry may need explicit strategies that generate valid data. Test invalid examples and public encoding as well as schema-generated valid examples. See [plugin tests](../testing/) and [result handling](../../use/results/).

## Declare location ownership

Mark one `LocationDependency` as `primary=True` to assign output rows to that input’s locations. Every output then requires non-null string `location_id` and `row_key` columns. Each output’s `location_id` must occur in the primary input, whose [provider scope](../location-providers/#define-revision-coverage) identifies the dataset and revision.

Reserve these column names for their structural roles:

- `location_id`: record or row-owner ID
- `source_location_id`: operation source ID
- `row_key`: metric row key within one owner and output
- `geometry`: active geometry column

Declare `unique=["location_id", "row_key"]` on each output schema. Choose keys that identify the calculation’s rows consistently, regardless of row order. For example, use `row_key="area"` when each location owns one area measurement.

Without a primary input, the metric is unpartitioned: it calculates over the complete selection without assigning output rows to locations. Use this scope for summaries, including summaries that consume location-scoped metric outputs.

### Handle shared targets and references

[Operation mappings](../location-operations/) can associate several sources with the same target. Lyra forwards every source/target pair to the metric. Choose the rows your calculation needs:

- Deduplicate `location_id` when calculating once per target record
- Keep every pair and use distinct stable keys when calculating relationships
- Keep independent reference selections complete, even when their IDs overlap the primary selection

Unpartitioned dependencies also retain their complete input context. Result collections are unordered, so sort them explicitly for presentation.

You’re responsible for deterministic results and semantic correctness. Declare all dependencies and origins, choose stable keys, and honor any independence promise you make. Structural validation cannot prove those properties. For resource sharing across acquisition and calculation, follow [resource lifecycle rules](../resources/#manage-resource-clients-and-credentials).
