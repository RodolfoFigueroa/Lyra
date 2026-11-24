---
title: Export spatial tables to GIS files
description: Export retained spatial tables to GeoParquet and use ordinary GeoPandas file access.
contentType: How-to
contentPlan: use/location-exports
---

Export retained spatial outputs and location inputs to GeoParquet, or use GeoPandas to write edited tables and other GIS formats. Managed exports save one table and its description per file without running handlers or acquiring provider data. Choose names from `description.outputs` and `description.location_inputs` through [result access](../results/).

See the generated [synchronous export reference](../../api/client-exports/sync/) and [asynchronous export reference](../../api/client-exports/async/) for method signatures. Access these methods through `client.results`.

## Export retained tables

Install the dependencies for your access method:

- `lyra[runtime]` for local store access
- `lyra[client,data]` for remote native exports
- `lyra[client]` for ordinary client JSON access and Arrow byte downloads

The `data` extra includes GeoPandas with Parquet attribute persistence and PyArrow. The `runtime` extra includes these dependencies too.

Call `export_geoparquet()` for a named spatial output or `export_location_input_geoparquet()` for a saved input. Both take the job ID, table name, and a local destination (`str` or `pathlib.Path`). Create the parent directory first. On success, each method writes one file and returns `None`.

### Export from a local store

Use the store path and job ID from `result.metadata`. This function writes one spatial output and one location input to separate files.

```python title="export_local.py" {8-11}
from pathlib import Path

from lyra.runtime.results import ResultStore


def export_local(store_path, job_id, output_name, input_name):
    with ResultStore.open(store_path) as store:
        store.export_geoparquet(job_id, output_name, Path("output.parquet"))
        store.export_location_input_geoparquet(
            job_id,
            input_name,
            "input.parquet",
        )
```

### Export from a deployment

Supply the deployment address and your agent credential. These methods write files on your computer:

```python title="export_remote.py" {6-9}
from lyra.api import LyraClient


def export_remote(server, agent_api_key, job_id, output_name, input_name):
    with LyraClient(server, agent_api_key=agent_api_key) as client:
        client.results.export_geoparquet(job_id, output_name, "output.parquet")
        client.results.export_location_input_geoparquet(
            job_id, input_name, "input.parquet"
        )
```

### Export with the async client

Use the same arguments with `await` for the async client:

```python title="export_async.py" {8-13}
from pathlib import Path

from lyra.api import AsyncLyraClient


async def export_async(server, agent_api_key, job_id, output_name, input_name):
    async with AsyncLyraClient(server, agent_api_key=agent_api_key) as client:
        await client.results.export_geoparquet(
            job_id, output_name, Path("output.parquet")
        )
        await client.results.export_location_input_geoparquet(
            job_id, input_name, "input.parquet"
        )
```

### Choose a spatial selection

Select names from the retained description. For example, a result might expose:

- `inspection_points`: a named spatial output
- `blocks`: a direct input containing provider records
- `nearby_trees`: a correspondence input containing immediate source/target pairs

Correspondence inputs include target geometry and attributes. A target may appear under several sources. Export reads the retained selection, including cached and fresh portions.

Check the selected table’s availability and schema before exporting:

- Known empty spatial outputs and inputs export with their declared columns, scopes, and CRS
- Inputs with `availability="not_materialized"` raise `LocationInputUnavailableError` without triggering acquisition
- Nonspatial outputs raise `ValueError`, even when empty; export doesn’t join provider geometry onto them

Each export reads the retained values again, independently of your edits to previously retrieved frames or descriptions. Remote exports retrieve complete descriptions when inspection returns a preview. To save your edits, use the [GeoPandas writing example](#write-edited-frames-and-other-gis-formats).

## Destination and failure behavior

Each export atomically replaces its destination after a successful write. To complete an export, Lyra:

1. Retrieves the retained table and description, then closes retrieval resources
2. Writes and closes a temporary file in the destination directory
3. Replaces the destination with the completed file

Failure or cancellation removes temporary files and preserves an existing destination. If the destination didn’t exist, it stays absent. Each call commits separately, so exporting an output and input isn’t a single transaction.

Handle the same lookup errors as other [result retrieval methods](../results/):

- A malformed job ID raises a validation error
- Missing targets raise not-found errors
- Broken required relationships in stored data raise integrity errors

Filesystem and writer errors also propagate unchanged. These include `FileNotFoundError` for a missing parent and `PermissionError` for denied access.

When the async task receives cancellation, it cleans up retrieval resources and temporary files. Native conversion and writing run synchronously, so cancellation may not interrupt that work immediately.

## Read the file and its description

Read each file with GeoPandas, then inspect its CRS and embedded Lyra description:

```python title="read_export.py" {4-5,10}
import geopandas as gpd

output = gpd.read_parquet("output.parquet")
metadata = output.attrs["lyra"]
selected = metadata["description"]
print(metadata["job_id"])
print(selected["table_schema"], output.crs)

locations = gpd.read_parquet("input.parquet")
input_description = locations.attrs["lyra"]["description"]
print(input_description["kind"], input_description["provider_scope"])
```

Use `attrs["lyra"]` to find the selected description and its job ID. The [GeoParquet metadata reference](../../reference/location-artifacts/#geoparquet-metadata) explains the fields, provider scopes, and structural IDs.

GeoParquet preserves declared columns and CRS, including for empty tables. Lyra writes with `index=False`; don’t use a reconstructed pandas index as record identity.

## Read the report for an exported table

Read the job ID from the file’s metadata. Pass it to the original store to retrieve the full description and execution report.

```python title="follow_export.py" {6-7}
from lyra.runtime.results import ResultStore


def follow_export(store_path, metadata):
    with ResultStore.open(store_path) as store:
        description = store.describe(metadata["job_id"])
        report = store.read_report(metadata["job_id"])
    return description, report
```

For remote access, connect to the associated deployment and call these methods:

- `client.results.describe(metadata["job_id"])` for the complete description
- `client.reports.get(metadata["job_id"])` for the execution report

Await both calls with the async client. You can retrieve reports after transient jobs expire, while the retained store remains available and compatible.

The file doesn’t contain reports or the store’s location. Supply the store path or deployment address yourself. Reading the file’s metadata doesn’t register a provider or bind a managed execution.

## Write edited frames and other GIS formats

Use GeoPandas to save your edited tables or convert them to other GIS formats. This example reprojects the output and saves it as Parquet, GeoPackage, and Shapefile. Clear the retained metadata because it describes the original table:

```python
output = gpd.read_parquet("output.parquet")
output = output.to_crs(4326)
output.attrs.clear()
output.to_parquet("edited.parquet", index=False)
output.to_file("points.gpkg", layer="points", driver="GPKG", index=False)
output.to_file("points.shp", driver="ESRI Shapefile", index=False)
```

GeoPandas and the selected driver determine which values they support and how they convert and preserve them. Direct writes use the library’s file behavior, without Lyra’s atomic replacement guarantee. Third-party rewrites may discard custom metadata.

Files without Lyra metadata remain readable spatial files. File bytes or checksums don’t establish provider identity. See the [native table reference](../../reference/location-artifacts/) for metadata and preservation details.
