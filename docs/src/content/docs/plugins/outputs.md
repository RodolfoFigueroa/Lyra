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
from lyra.sdk import Unit, metric


class Coverage(pa.DataFrameModel):
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

```python {4}
@metric(
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
    }
)
```

For each column, declare:

- A fixed, unique string name
- An explicit data type (`dtype`)
- A nonblank description
- A unit in `metadata["unit"]`, including `None` when no unit applies

Lyra rejects optional columns and names defined by regular expressions. Tables must contain exactly the declared columns. Lyra reorders columns to declaration order before validation.

Choose a supported dtype:

- Integer and floating-point types through 64 bits, including pandas nullable equivalents
- Booleans and strings
- Dates and timestamps, with or without timezones
- Categoricals containing supported scalar values
- Geometry

Lyra rejects unrestricted object, complex, nested, and custom extension dtypes. Explicit string and date schemas can validate object-backed values.

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
)
```

Use `nullable=True` to allow missing values. Empty tables must retain their declared columns and dtypes. Lyra rejects infinities.

Construct nullable integers with pandas’ `Int64` dtype. These frames both satisfy the `observations` declaration:

```python {3-4}
import pandas as pd

all_null = pd.DataFrame({"count": pd.Series([None], dtype="Int64")})
empty = pd.DataFrame({"count": pd.Series([], dtype="Int64")})
```

Use compatible scalar types within each set of category values. For example, use strings throughout a category set; mixing strings with numbers fails validation. Lyra checks unused categories and empty outputs too.

Lyra resets producer indexes to a fresh `RangeIndex` before validation, preserving row order without mutating the producer frame. Explicit index declarations are rejected. Declare identifiers and uniqueness constraints, including compound keys, in columns.

Use built-in constraints, uniqueness rules, and named deterministic Python checks that operate on the supplied data:

```python
positive_total = pa.Check(
    lambda frame: frame["count"].sum() >= 0,
    name="positive_total",
    description="Total count is nonnegative.",
)
```

DataFrame checks can enforce relationships between columns. Lyra rejects checks that only warn, parsers, column filtering, missing-column insertion, and row dropping. Execution always validates the full schema and data, regardless of global Pandera settings. For diagnostics, call `plugin.normalize_result(name, value, lazy=True)`.

## Declare spatial outputs

Declare exactly one geometry column and return a GeoDataFrame with that active geometry and a known coordinate reference system (CRS):

```python {9}
from pandera.engines.geopandas_engine import Geometry

places = pa.DataFrameSchema(
    {
        "shape": pa.Column(
            Geometry(crs="EPSG:4326"),
            nullable=True,
            description="Source location.",
            metadata={"unit": None},
        ),
    }
)
```

Pandera enforces an explicit CRS constraint. A geometry dtype without a CRS constraint accepts any known CRS. Empty spatial tables still need active geometry and CRS metadata.

## Describe units and inspect metadata

Units are descriptive metadata. Supply an existing `Unit` member, its canonical value, or `None`. Units do not convert numbers or enforce ranges. See the [unit vocabulary](../../reference/units/).

Lyra generates manifests and application programming interface (API) metadata from the registered schema snapshot. These descriptions include column constraints, units, and geometry.

Built-in check parameters use JavaScript Object Notation (JSON) values. Python checks appear by name and description; metadata cannot reconstruct their executable validation. Lyra loads catalogs without importing plugin implementations.

## Return and encode validated tables

Return one DataFrame directly for one output, or a dictionary with exactly the declared names for several outputs. Include empty tables: every declared output must validate before an invocation succeeds.

Repeated graph references reuse the validated invocation result. Treat dependency inputs as read-only and copy them before mutation.

Tables passed between metrics retain declared data types, including columns with no rows or only nulls. Arrow interprocess communication (IPC) cache artifacts preserve those types too.

Check any consumer assumptions beyond the producer contract, such as required properties or supported geometry. [Graph execution errors](../../concepts/graphs/#stop-at-the-first-failure) identify the failing node and dependency path.

Change `cache_revision` when source data or unversioned implementation code changes. Output validation checks the current schema; it cannot detect either change. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions).

Schema validity and serialization support are distinct requirements. Ordinary tables become records; spatial tables become GeoJSON in World Geodetic System 1984 (WGS84). Missing values become null, dates and timestamps become ISO strings, and categorical values become scalars.

Publish identifiers as columns and align dependencies using those columns. Public decoding does not restore native pandas dtypes.

For isolated plugin tests, use `schema.strategy()` or `Model.to_schema().strategy()` with Hypothesis. Custom checks and geometry may need explicit strategies that generate valid data. Test invalid examples and public encoding as well as schema-generated valid examples. See [plugin tests](../testing/) and [result handling](../../use/results/).
