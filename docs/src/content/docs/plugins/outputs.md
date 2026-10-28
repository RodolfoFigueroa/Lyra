---
title: Declare output tables and units
description: Return named DataFrames or GeoDataFrames with useful column descriptions and explicit units.
contentType: How-to
contentPlan: plugins/outputs
sidebar: { label: "Declare Output Tables And Units" }
---

Every metric declares a nonempty `outputs` mapping. Each key names a whole table. The declaration describes that table’s columns, units, and optional geometry.

## Choose output names and return shapes

A node with one output can return its DataFrame or GeoDataFrame directly. A node with several outputs returns a dictionary with exactly the declared keys.

For example, the repository’s population baseline publishes `example_population_areas` and `example_population_total`. Its return value contains a spatial area table and a separate total table. Both are included when a caller runs that metric.

Return every declared table, including when it has zero rows. Preserve the columns of an empty table and the active geometry column and coordinate reference system (CRS) of an empty spatial table. `None` is not a valid output.

## Describe the columns

Use `TableOutput` and `TableColumn` from `lyra.sdk`:

```python
from lyra.sdk import TableColumn, TableOutput, Unit

population_output = TableOutput(
    columns=[
        TableColumn(
            name="area_code",
            type="string",
            unit=None,
            description="Source census area identifier.",
        ),
        TableColumn(
            name="population",
            type="integer",
            unit=Unit.COUNT,
            description="Residents in the entire source census area.",
        ),
    ],
    geometry="geometry",
)
```

Supported column types are `integer`, `number`, `string`, `boolean`, `date`, and `datetime`. Use column descriptions to explain meaning, scale, and established missing-value semantics. Set nullability when appropriate.

The geometry declaration identifies the geometry column; the returned GeoDataFrame supplies its CRS. Leave `geometry` unset for an ordinary table.

## Select units deliberately

Every column must explicitly supply `unit`. Use a `Unit` member, its exact canonical string, or `None` when no unit applies. For example, an area column can use `Unit.SQUARE_METRE`; an identifier uses `None` when its values have no unit.

A unit explains what a value means without converting the number, enforcing a range, or constraining its column type. If your calculation requires a ratio between zero and one, enforce that rule in the calculation or its validation.

See the [unit vocabulary](../../reference/units/) for all supported values. Establish the scientific meaning before selecting a unit.

## Preserve the producer’s row identity

The producer owns row counts, order, indices, and identifiers. Publish identifiers as ordinary columns so they survive server serialization. Do not assign request feature IDs to unrelated result rows.

Plugins calculate joins, area fractions, aggregates, and density denominators explicitly. A consumer decides how to align the returned tables with other data.

## Understand validation and encoding

Runtime checks enforce native table types and exact output names. Column metadata describes the result; it does not enforce cell schemas, column order, or row identity.

Native dependency tables remain unchanged inside a graph. Treat received tables as read-only and make an explicit copy before mutation.

For server results, ordinary tables become record arrays and spatial tables become GeoJSON using World Geodetic System 1984 (WGS84). Native indices are omitted. Missing cells become null; dates and timestamps become ISO strings. Missing spatial CRS or unsupported values cause serialization to fail.

[Result handling](../../use/results/) explains what clients receive. Use [plugin tests](../testing/) to check both native returns and public encoding.
