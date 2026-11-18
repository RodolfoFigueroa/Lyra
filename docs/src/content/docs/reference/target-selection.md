---
title: Select and map target candidates
description: Look up selector signatures, supported Ibis filters, and mapping rules.
contentType: Reference
contentPlan: reference/target-selection
sidebar: { label: "Select And Map Target Candidates" }
---

Use this reference when you add `target_selector` to a location operation. The selector builds a filter for one [managed provider](../../plugins/location-providers/#use-managed-parquet-and-postgis-providers). Lyra acquires the matching candidates, then the mapping handler assigns them to source locations.

The examples assume two registered providers:

| Provider ID | Records | CRS |
| --- | --- | --- |
| `managed_blocks` | Blocks with `location_id` and `geometry` | EPSG:4326 |
| `managed_trees` | Trees with `location_id`, `geometry`, and numeric `height` in metres | EPSG:32614, with metre distance units |

## Callback arguments

Lyra calls both callbacks by position, using the following arguments:

| Callback | First argument | Second argument | Return value |
| --- | --- | --- | --- |
| Target selector | Source `GeoDataFrame` | Unbound `ibis.expr.types.Table` | Filtered Ibis table |
| Mapping handler | Source `GeoDataFrame` | Candidate `GeoDataFrame` | `dict[str, tuple[str, ...]]` |

The unbound table represents the target provider’s schema without loading its records. Lyra executes the returned expression through that provider before invoking the mapping handler.

Argument names can differ between callbacks and from the source declaration. For example, declare `blocks`, select with `sources, trees`, and map with `records, pool`. Bindings still use `blocks`.

Both callbacks either omit `parameters` or accept it as a keyword-only argument with the same `MetricParameters` subclass. Lyra infers the model from the mapping handler. The [callback signature rules](../parameters/#operation-callback-signatures) describe registration validation.

## Supported filters

A selector returns the supplied table unchanged or applies filters that preserve every registered column and value. These unparameterized selectors demonstrate select-all and attribute filtering:

```python title="target_selection.py" {6,10}
import geopandas as gpd
import ibis.expr.types as ir


def select_all_trees(sources: gpd.GeoDataFrame, trees: ir.Table) -> ir.Table:
    return trees


def select_tall_trees(sources: gpd.GeoDataFrame, trees: ir.Table) -> ir.Table:
    return trees.filter(trees.height > 5)
```

A Boolean expression alone does not satisfy the return contract. Supported predicates include:

- Comparisons and Boolean composition
- Null tests and membership in literal collections
- Row-wise spatial predicates supported by the provider’s backend

Chained filters are valid. Under backend null semantics, only a true predicate selects a row.

The following expression forms are unsupported:

- Other relations, joins, and subqueries
- Aggregates and windows
- Projections and computed output columns
- Unions, sorting, limits, offsets, and sampling
- Raw SQL expressions and user-defined functions

Unsupported backend operations fail instead of falling back to Python filtering. Don’t materialize the symbolic table with `execute()`, `to_pandas()`, or another materialization method.

## Spatial filters and parameters

Spatial comparisons require coordinates and units compatible with the target CRS. Reproject source geometry in both callbacks when the source and target CRSs differ. Lyra performs neither automatic reprojection nor distance-unit conversion.

The following parameter model defines height and distance thresholds in metres. Both callbacks below accept this model:

```python title="target_selection.py"
import ibis
import ibis.expr.datatypes as dt
from pydantic import Field
from lyra.sdk import LocationDependency, MetricParameters, location_operation


class TreeParameters(MetricParameters):
    minimum_height: float = Field(5.0, ge=0, description="Exclusive height threshold")
    maximum_distance: float = Field(100.0, ge=0, description="Inclusive distance limit")
```

This selector transforms blocks into the target CRS before finding trees near any block. The geometry literal’s SRID must match the target CRS; assigning an SRID alone doesn’t transform coordinates:

```python title="target_selection.py" {5-8,11-13}
def select_nearby_trees(
    sources: gpd.GeoDataFrame, trees: ir.Table, *, parameters: TreeParameters
) -> ir.Table:
    if sources.empty:
        return trees.filter([False])
    projected = sources.to_crs("EPSG:32614")
    area = ibis.literal(projected.geometry.union_all(), type=dt.GeoSpatial(srid=32614))
    return trees.filter(
        trees.height > parameters.minimum_height,
        trees.geometry.d_within(area, distance=parameters.maximum_distance),
    )
```

## Mapping and correspondence rules

Write a filter that includes every candidate your mapping needs. Lyra acquires those candidates, and the handler chooses each source’s correspondences: pairs of source and target IDs. Declare both callbacks on one operation, with a fixed target provider:

```python title="target_selection.py" {6-8}
@location_operation(
    name="nearby_trees",
    code_revision="1",
    description="Map blocks to mature trees within the requested distance.",
    inputs={"blocks": LocationDependency(provider="managed_blocks")},
    target_provider="managed_trees",
    target_selector=select_nearby_trees,
)
```

Place this handler immediately after the decorator. It reprojects its own working frame and refines the shared candidate pool separately for each block:

```python title="target_selection.py" {7-10,14-17}
def nearby_trees(
    records: gpd.GeoDataFrame,
    pool: gpd.GeoDataFrame,
    *,
    parameters: TreeParameters,
) -> dict[str, tuple[str, ...]]:
    distinct = records.drop_duplicates("location_id")
    if pool.empty:
        return dict.fromkeys(distinct.location_id, ())
    projected = distinct.to_crs("EPSG:32614")
    return {
        identifier: tuple(
            sorted(
                pool.loc[
                    pool.geometry.distance(geometry) <= parameters.maximum_distance,
                    "location_id",
                ]
            )
        )
        for identifier, geometry in zip(
            projected.location_id, projected.geometry, strict=True
        )
    }
```

The mapping must satisfy these rules:

- Keys belong to the supplied source selection
- Target IDs belong to this invocation’s original validated candidates
- Each source group contains unique target IDs
- Different sources may share target IDs

Lyra forwards the validated candidate records without a second exact-ID lookup. An [ID-only operation](../../plugins/location-operations/) instead resolves its returned IDs through the target provider’s exact-ID path.

Operation-derived inputs can repeat a block under different incoming source locations. This handler deduplicates blocks because its calculation uses each block’s geometry. Keep correspondence rows when your calculation depends on their relationships.

Each source location owns its correspondence group, called a result partition. Candidates are not another declared input, and results must not depend on row order.

If you promise per-location independence, changing the other sources in a batch must leave each source’s result unchanged. That promise covers both selection and mapping, even when the shared candidate pool grows.

## Select from a catalog with interventions

Write your selector against the supplied target relation. When a request changes the target catalog, the relation contains the remaining base records and the additions. Returning the relation selects all those records. Applying a filter tests both base records and additions in the same backend.

The source and target catalogs can each have their own intervention set. Your operation receives the source records after those changes. With a selector, map only IDs from the candidate frame. Lyra uses the matching target records as inputs to subsequent operations.

A filter can return zero matches. If an ID-only operation requests a removed target by ID, resolution fails.

DuckDB and PostGIS load additions and removal IDs on the first predicate query for each affected catalog. Later selectors and batches reuse that temporary data within the run. Exact-ID reads and membership checks need no staging. Lyra removes temporary objects before the backend closes, including after failure or cancellation. See [PostGIS privileges](../../plugins/location-providers/#postgis-relation-configuration).

## Working copies and empty selections

Each callback receives independent working frames and parameter instances, including detached nested values. Selector edits cannot change the mapping handler’s inputs or retained computation identity. Mapping edits cannot change the allowed candidate IDs or authoritative records that Lyra forwards.

Lyra handles empty selections according to the source and candidate counts:

| Condition | Behavior |
| --- | --- |
| Empty source selection | Complete with an empty mapping; skip both callbacks and candidate acquisition |
| Nonempty sources, no candidates | Invoke the handler with an empty candidate frame that retains the registered schema and CRS |
| Acquisition or validation failure | Fail the operation; do not return a successful empty selection |

With no candidates, return empty tuples or omit source keys to accept no matches. Raise a domain error if your operation requires a match.

## Resource access and revisions

Keep both callbacks computational. They cannot declare:

- Resource bindings or direct origins
- Metric-table dependencies or extra location inputs

Don’t read files, query databases, or make network requests inside either callback. You’re responsible for this restriction; Lyra doesn’t sandbox Python callbacks. Metrics and ordinary resources keep their [direct-acquisition contract](../../plugins/resources/).

The operation declaration fixes the target provider. Changing source selections cannot redirect it. Advance the operation’s `code_revision` whenever selection or mapping behavior changes; there is no separate selector revision or expression hash.
