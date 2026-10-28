---
title: Use runtime services
description: Access logging, scratch storage, progress reporting, and dataset acquisition services.
contentType: How-to
contentPlan: plugins/runtime
sidebar: { label: "Use Runtime Services" }
---

When your handler needs logging, scratch storage, or acquisition services, declare the context that matches its role. Metrics use `context: RunContext`, while datasets can use `context: DatasetContext` to access database services as well:

| Service | Available to |
| --- | --- |
| `job_id` and `node` | Metrics and datasets |
| `logger` | Metrics and datasets |
| `temp_dir` | Metrics and datasets |
| `report_progress(...)` | Metrics and datasets |
| `db` | Datasets |

## Log diagnostics and use scratch storage

Use `context.logger` for diagnostics. It supports ordinary Python logging arguments and attaches the job ID and node name to worker log records.

Use `context.temp_dir` for internal scratch files. Return native tables, with all needed data available in memory. Server scratch files can outlive result retention; operators manage their cleanup. The default local runner removes scratch storage before returning.

## Report progress

For example, inside a handler that declares a context:

```python
context.report_progress(
    stage="areas",
    current=10,
    total=100,
    unit="areas",
    message="Processed the first ten areas.",
)
```

`current` must be finite and nonnegative. A supplied `total` must be finite and positive, and `current` cannot exceed it.

Progress is an estimate: stages, totals, and units may change, and estimates may decrease. Updates are coalesced using `jobs.progress_min_interval_ms` (default 1000 ms). Persistence failures do not fail the calculation.

Keep progress optional. Clients use lifecycle status to detect completion. Progress units are human-readable labels, separate from output column units.

## Query a database from a dataset

Use bound parameters for values:

```python
frame = context.db.query_dataframe(
    "SELECT area_code, population FROM example_city_census WHERE year = :year",
    params={"year": 2020},
)
```

For a spatial query, call `query_geodataframe(..., geom_col="geometry")`. Lyra manages connections. The plugin owns source names and query logic.

When you omit `crs`, GeoPandas tries to infer the coordinate reference system (CRS) from the first non-null geometry’s spatial reference identifier (SRID). If the result has no usable SRID, supply the source’s CRS explicitly. Setting `crs="EPSG:6372"` labels coordinates without transforming them and also supplies metadata for empty or all-null geometry results.

A metric obtains domain data through declared table dependencies. Its context does not expose a database client.

## Use Earth Engine in datasets

Normal workers initialize Earth Engine before loading plugins. Operators supply `earth_engine.project` and `earth_engine.service_account_file`, including permissions for any private assets. Datasets use that initialized environment.

Keep imports and plugin factories usable without initialization. Create initialization-dependent images, collections, reducers, and expressions during dataset execution. Keep authentication and application configuration outside handlers; metric parameters describe the calculation’s inputs.

The helper `lyra.utils.ee.reduce_ee_image_over_gdf` reprojects geometry to EPSG:4326. Its `scale` is reduction resolution in metres. Preserve the workflow’s established dataset, reducer, and resolution.

## Provide services outside workers

Standalone SDK calls do not run worker initialization. A local application supplies its own context factory and owns the resources it creates. See [local execution](../../use/local-execution/#supply-dataset-services).

Use strict fakes or mocks for offline tests. An authorized live calculation needs a separately configured harness; importing the plugin or generating its manifest must remain possible without contacting sources.

`LyraDB` is a handwritten protocol with `query_dataframe` and
`query_geodataframe` methods. It defines query signatures without owning an
engine. Local contexts may supply any implementation that satisfies this
protocol; the application database client owns its engine reference.
