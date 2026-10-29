---
title: Declare output tables and units
description: Validate named DataFrames and GeoDataFrames with Pandera contracts.
contentType: How-to
contentPlan: plugins/outputs
sidebar: { label: "Declare Output Tables And Units" }
---

Every metric declares a nonempty `outputs` mapping from output names to Pandera models or schema objects. Lyra compiles independent snapshots during registration and validates every executed producer before its consumers run.

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

Lyra rejects optional columns and names defined by regular expressions. Tables must contain exactly the declared columns. Set `ordered=True` to require their declared order.

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
    index=pa.Index(int, unique=True),
)
```

Use `nullable=True` to allow missing values. Empty tables must retain their declared columns and dtypes. Lyra rejects infinities.

Without an index schema, Lyra imposes no index restrictions. Validation preserves row order and index semantics, but may return a different table object.

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

Lyra generates manifests and application programming interface (API) metadata from the registered schema snapshot. These descriptions include column constraints, units, geometry, and optional indices.

Built-in check parameters use JavaScript Object Notation (JSON) values. Python checks appear by name and description; metadata cannot reconstruct their executable validation. Lyra loads catalogs without importing plugin implementations.

## Return and encode validated tables

Return one DataFrame directly for one output, or a dictionary with exactly the declared names for several outputs. Include empty tables: every declared output must validate before an invocation succeeds.

Repeated graph references reuse the validated invocation result. Treat dependency inputs as read-only and copy them before mutation.

Schema validity and serialization support are distinct requirements. Ordinary tables become records; spatial tables become GeoJSON in World Geodetic System 1984 (WGS84). Missing values become null, dates and timestamps become ISO strings, and categorical values become scalars.

Publish identifiers as columns because native indices remain internal. Public decoding does not restore native pandas dtypes.

For isolated plugin tests, use `schema.strategy()` or `Model.to_schema().strategy()` with Hypothesis. Custom checks and geometry may need explicit strategies that generate valid data. Test invalid examples and public encoding as well as schema-generated valid examples. See [plugin tests](../testing/) and [result handling](../../use/results/).
