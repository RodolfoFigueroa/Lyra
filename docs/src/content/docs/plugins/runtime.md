---
title: Use runtime services
description: Access logging, scratch storage, progress reporting, and data acquisition services.
contentType: How-to
contentPlan: plugins/runtime
sidebar: { label: "Use Runtime Services" }
---

Declare `context: RunContext` when your metric needs logging, scratch storage, progress reporting, or database access:

| Service | Use it to |
| --- | --- |
| `job_id` and `node` | Identify the current job and metric |
| `logger` | Log diagnostics |
| `temp_dir` | Store scratch files |
| `report_progress(...)` | Report progress |
| `db` | Query the configured database; the worker creates the client on first access |

## Log diagnostics and use scratch storage

Use `context.logger` for diagnostics. It supports ordinary Python logging arguments and attaches the job ID and node name to worker log records.

Use `context.temp_dir` for internal scratch files. Return native tables, with all needed data available in memory. Server scratch files can outlive result retention; operators manage their cleanup. The default local runner removes scratch storage before returning.

## Report progress

Call `context.report_progress(...)` to report the current stage and completed work:

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

Progress is an estimate: stages, totals, and units may change, and estimates may decrease. Lyra combines frequent updates using `jobs.progress_min_interval_ms` (default 1000 ms). Persistence failures do not fail the calculation.

Keep progress optional. Clients use lifecycle status to detect completion. Progress units are human-readable labels, separate from output column units.

## Query a database from a metric

Use `context.db` to query PostgreSQL from a metric. Ask your deployment operator to [configure PostgreSQL access](../../operate/configuration/#configure-postgresql-access) before running the metric.

The worker creates a database client when your handler first accesses `context.db`. Without database configuration, that access fails the job with `database_not_configured` and `retryable=false`. Other context services work without PostgreSQL.

Use bound parameters for values:

```python
frame = context.db.query_dataframe(
    "SELECT area_code, population FROM example_city_census WHERE year = :year",
    params={"year": 2020},
)
```

For a spatial query, call `query_geodataframe(..., geom_col="geometry")`. Lyra manages connections. The plugin owns source names and query logic.

When you omit `crs`, GeoPandas tries to infer the coordinate reference system (CRS) from the first non-null geometry’s spatial reference identifier (SRID). If the result has no usable SRID, supply the source’s CRS explicitly. Setting `crs="EPSG:6372"` labels coordinates without transforming them and also supplies metadata for empty or all-null geometry results.

You can query sources within a metric or reuse another metric’s tables through [dependencies](../dependencies/).

## Use Earth Engine in metrics

Workers initialize Earth Engine before loading executable plugins when the deployment configuration includes `[earth_engine]`. Omitting the section disables initialization for all workers using the document.

If your metric needs Earth Engine, ask your deployment operator to [configure its project and credentials](../../operate/configuration/#enable-earth-engine-for-workers). Every worker needs a read-only credential mount, and the service account needs access to any private assets.

Keep imports and plugin factories usable without initialization. Create initialization-dependent images, collections, reducers, and expressions during metric execution. Keep authentication and application configuration outside handlers; metric parameters describe the calculation’s inputs.

The helper `lyra.utils.ee.reduce_ee_image_over_gdf` reprojects geometry to EPSG:4326. Its `scale` is reduction resolution in metres. Preserve the workflow’s established dataset, reducer, and resolution.

## Provide services outside workers

Standalone SDK calls do not run worker initialization. A local application supplies its own context factory and owns the resources it creates. See [local execution](../../use/local-execution/#supply-runtime-services).

Use strict fakes or mocks for offline tests. An authorized live calculation needs a separately configured harness; importing the plugin or generating its manifest must remain possible without contacting sources.

`LyraDB` defines the `query_dataframe` and `query_geodataframe` methods without managing an engine. Your local context can supply any implementation that satisfies this protocol.
