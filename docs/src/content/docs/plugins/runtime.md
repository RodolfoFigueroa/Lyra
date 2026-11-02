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

Implement database access in a `BaseIO` subclass in your plugin. Register it in `PluginDefinition(resources=...)` and request it with `Annotated[YourDatabaseIO, Resource("your_source")]`. Your resource defines:

- Connection settings and credential references
- Client dependencies, pools, and timeouts
- Queries and cleanup

Create clients in `setup()` and release them in `close()`. Wrap reads in `operation()` and bind SQL query parameters. Configure connections and credentials through [named resource settings](../../operate/configuration/#configure-plugin-resources); Lyra initializes resources when execution requests them.

The [population example](../dependencies/) registers `smoke_plugin.resources.CensusDatabaseIO` as `census_db`. Its query methods return pandas and GeoPandas tables.

GeoPandas infers the coordinate reference system (CRS) from spatial reference identifiers (SRIDs) in returned geometries. Set an explicit CRS to label coordinates without transforming them, including when results contain no geometry.

## Use Earth Engine in metrics

Manage Earth Engine access through a resource in your plugin. Declare `earthengine-api` as a dependency; also select `lyra-utils[earth-engine]` if you use `lyra.utils.ee`.

Define the project setting, declare the credential secret, and initialize the client in `setup()`:

```python
import ee
from lyra.sdk import BaseIO, IOConfig
from pydantic import Field


class EarthEngineSettings(IOConfig):
    project: str = Field(min_length=1)


class EarthEngineIO(BaseIO[EarthEngineSettings]):
    config_model = EarthEngineSettings
    secret_names = ("service_account_json",)

    def setup(self) -> None:
        credentials = ee.ServiceAccountCredentials(
            key_data=self.secrets["service_account_json"]
        )
        ee.Initialize(credentials, project=self.config.project)

    def close(self) -> None:
        ee.Reset()
```

Connect the resource to your metric:

1. Add methods that complete Earth Engine requests inside `with self.operation("read"):`. Construct images, collections, and reducers in these methods.
2. Register the class with `PluginDefinition(resources={"elevation": EarthEngineIO}, ...)`.
3. Request it in a handler argument annotated with `Annotated[EarthEngineIO, Resource("elevation")]`.

Keep credentials and project settings in [named resource configuration](../../operate/configuration/#configure-plugin-resources), separate from metric parameters.

Configure this example resource through named settings and a secret reference:

```toml
[resources.elevation.config]
project = "your_project_id_here"

[resources.elevation.secrets.service_account_json]
file = "/lyra_data/secrets/elevation.json"
```

Mount the credential file read-only on workers executing these metrics, and grant the service account access to the requested assets. You can also supply the JSON secret through an environment reference. For local execution, pass the equivalent `ResourceConfig` to `LocalRunner` through `resource_config`.

Lyra initializes requested resources during execution and closes them afterward, including after setup failures. Credential or initialization failures stop the affected execution. Keep imports, factories, and manifest generation free of credential access and initialization.

The Earth Engine client shares state across a process, so this example supports one configuration per executing process. Coordinate initialization and cleanup across plugins that share that state. Creating separate resource instances does not isolate their clients.

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
    metrics=[observations],
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

Register a concrete `LocationProvider` subclass as a named resource and designate that resource with `location_provider`:

```python
plugin = PluginDefinition(
    resources={"places": PlacesIO},
    location_provider="places",
)
```

Define these members on `PlacesIO`:

- `collections`: a nonempty tuple of `LocationCollection` declarations
- `outputs`: an executable location schema for each collection and unit type
- `contracts`: a nonempty tuple of concrete `LocationContract` values
- `resolve(contract, ids)`: a synchronous method that returns the selected observation’s geometry and attributes

Follow [Define a location provider](../location-providers/) for declaration fields, contract selection, validation, and acquisition errors.

A provider plugin may omit `metrics`; a mixed plugin supplies both `metrics` and `location_provider`. A plugin must declare at least one metric, location operation, or location provider.

`lyra-plugin describe --json` and the generated manifest expose the provider’s resource name, collections, and concrete contracts without initializing resources or reading credentials. Configure the designated resource through the existing named resource settings and secrets.

Exactly one enabled plugin may designate a provider. `LocalRunner` applies the same rule to its selected plugins. Lyra rejects multiple declarations, including when they name the same resource. Plugins without location resolution need no provider, and inspection never acquires one.

## Declare a location operation

Decorate a synchronous function with `@location_operation` and return one `GeoDataFrame`. Register it through `PluginDefinition`’s `location_operations` argument. A plugin can include operations, metrics, and a location provider.

Supply these decorator arguments:

- `name` and `description`: identify and describe the operation
- `locations`: map each named location argument to its concrete contract
- `output`: declare the result’s executable schema
- `output_contract`: identify the result’s concrete contract

For provider-backed inputs and outputs, reuse the contracts registered by the provider. Give constructed entities, such as buffers, a distinct concrete contract. Declare their columns and units in the output family and set the coordinate reference system (CRS) on the returned frame.

Return annotations describe Python types. The decorator defines the contract Lyra validates during execution.

The required `output` accepts a Pandera schema or model with `id` and `geometry` columns. Pass `LocationOutputSchema(schema, id_column=..., geometry_column=...)` for custom column names. Declare descriptions and unit metadata for all columns. Operations support typed parameters, location arguments, `inputs` table dependencies, and managed resources.

Operations may declare `context: LocationContext` for resolver access and reporting, `context: RunContext` for reporting, or omit context. `context.resolve_locations(collection, unit_type, ids)` shares the resolver and resource scope used for initial selections. It deduplicates IDs in first-occurrence order; empty lookups validate scope and schema without acquiring the provider.

Decoration preserves direct calls. To validate a supplied invocation without graph execution, use `prepare_resolved_invocation(...)` and `invoke_operation(...)` with its dependency tables and a context factory. The returned locations include declared schema coercion. Direct calls to the decorated function do not apply those checks.

Operations appear in plugin descriptions and manifests; public requests select table-producing metrics. Follow [Derive locations inside a graph](../location-operations/) to bind operation results, convert table rows, and inspect mixed graphs.
