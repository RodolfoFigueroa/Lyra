---
title: Use runtime services
description: Access logging, scratch storage, progress reporting, and data acquisition services.
contentType: How-to
contentPlan: plugins/runtime
sidebar: { label: "Use Runtime Services" }
---

Use `RunContext` for logging, scratch storage, and progress reporting. A resource groups file or service operations with their configuration and credentials. Request one when your metric needs database queries, file reads, or downloads. Lyra initializes each requested resource before invoking the node and closes it when the job ends.

Declare `context: RunContext` to access these services:

| Service | Use it to |
| --- | --- |
| `job_id` and `node` | Identify the current job and metric |
| `logger` | Log diagnostics |
| `temp_dir` | Store scratch files |
| `report_progress(...)` | Report progress |

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

Request the `database` resource to query PostgreSQL from a metric. Ask your deployment operator to [configure PostgreSQL access](../../operate/configuration/#configure-postgresql-access) before running it.

The worker initializes the database resource before invoking a node that requests it. Without database configuration, that access fails the job with `database_not_configured` and `retryable=false`. Other context services work without PostgreSQL.

Add a typed database argument to your metric and use bound parameters for query values. This function shows the argument and query; see [Connect metrics and dependencies](../dependencies/) for its decorator and output declaration:

```python
from typing import Annotated

from lyra.sdk import DatabaseIO, Resource


def city_source(
    database: Annotated[DatabaseIO, Resource("database")],
):
    return database.query_dataframe(
        """SELECT area_code, population FROM example_city_census
        WHERE year = :year""",
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

Pass `resource_config` to `LocalRunner` to configure resources outside a worker. For tests, pass fake resource classes through `resource_overrides`. The runner initializes and closes them; see [Supply runtime services locally](../../use/local-execution/#supply-runtime-services).

## Define an I/O resource

Define an input/output (I/O) resource to reuse file or service access across nodes. This example reads a Parquet file into a DataFrame. Your plugin environment needs pandas and a Parquet reader supported by pandas.

Declare the file settings and wrap the complete read in `operation()`:

```python
from pathlib import Path

import pandas as pd
from lyra.sdk import BaseIO, IOConfig


class FileSettings(IOConfig):
    directory: Path


class DataFrameIO(BaseIO[FileSettings]):
    config_model = FileSettings

    def read_parquet(self, name: str) -> pd.DataFrame:
        with self.operation("read_parquet"):
            return pd.read_parquet(self.config.directory / name)
```

A resource method may return any Python value, including a DataFrame. Finish reading and decoding inside the operation block. Lyra records the duration and outcome without serializing the result.

Declare the Parquet file’s output schema before registering the metric. This example expects one `float64` column containing demonstration values without a measurement unit:

```python {8}
import pandera.pandas as pa

observation_schema = pa.DataFrameSchema(
    {
        "value": pa.Column(
            float,
            description="Demonstration value.",
            metadata={"unit": None},
        )
    }
)
```

Request `shared_files` in the metric, then register the metric and resource class:

```python {9}
from typing import Annotated

from lyra.sdk import PluginDefinition, Resource, metric


@metric(
    name="observations",
    description="Read observations from a Parquet file.",
    outputs={"observations": observation_schema},
)
def observations(
    files: Annotated[DataFrameIO, Resource("shared_files")],
) -> pd.DataFrame:
    return files.read_parquet("observations.parquet")


plugin = PluginDefinition(
    nodes=[observations],
    resources={"shared_files": DataFrameIO},
)
```

Set the directory in `lyra.toml`. The configuration name must match the name in `Resource("shared_files")`:

```toml
[resources.shared_files.config]
directory = "/data"
```

Resource names apply across the deployment. Plugins that share a name must register the same class; they share one instance within each job. Use different names to configure separate instances of the same class.

Lyra initializes only the resources a job requests. When the job ends, Lyra closes them in reverse initialization order, including after a failure.

## Manage resource clients and credentials

Override lifecycle methods when your resource needs a service client:

- Use `setup()` to create and authenticate clients.
- Use `close()` to release clients, including any client created before `setup()` failed.
- Declare required secret names with `secret_names = ("token",)` and read their values from `self.secrets["token"]`.

Keep imports, registration, and manifest generation free of service calls and credential reads. Ask your deployment operator to [configure resource credentials](../../operate/configuration/#configure-plugin-resources) before running the metric.

For local file reads, wrap `path.read_bytes()` in `with self.operation("read_bytes"):`. For downloads, consume the complete response inside the block and configure timeouts on the service client.

## Register a location provider

Register a concrete `LocationProvider` subclass as a named resource and designate
that resource with `location_provider`:

```python
plugin = PluginDefinition(
    resources={"places": PlacesIO},
    location_provider="places",
)
```

`PlacesIO` declares its `collections` as a tuple of `LocationCollection` values,
each with a namespaced `name`, `unit_types`, and `crs`, and implements synchronous
`resolve(collection, unit_type, ids)`. A provider plugin may omit `nodes`; a mixed
plugin supplies both `nodes` and `location_provider`. A plugin must declare at
least one metric or a location provider.

`lyra-plugin describe --json` and the generated manifest expose the provider's
resource name and collection metadata without initializing resources or reading
credentials. Configure the designated resource through the existing named
resource settings and secrets.

Exactly one enabled plugin may designate a provider. `LocalRunner` applies the
same rule to its selected plugins. Multiple declarations are rejected, including
when they name the same resource. Plugins without location resolution need no
provider, and inspection never acquires one.
