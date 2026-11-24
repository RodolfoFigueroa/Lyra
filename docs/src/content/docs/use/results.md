---
title: Read and download results
description: Read saved metadata and execution reports, download remote tables, and reopen local results.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

Use the job ID to read saved tables, metadata, and [execution reports](../../concepts/provenance/), even after the queue record expires. Connect to the deployment with the Python client or open the SQLite file locally with `ResultStore`.

## Read a retained description

Pass your job ID to `client.results.describe()` with a [connected Python client](../python-client/#connect-and-inspect-a-metric). The description lists the named outputs and their schemas without loading any rows.

```python {1}
description = client.results.describe(job_id)
print(description.metric_name, description.parameters)
for name, output in description.outputs.items():
    print(name, output.table_schema, output.owner_scope)
```

Read these fields on the returned [`ResultDescription`](../../api/lyra/contracts/results/):

- `job_id`: identifies the saved result and its execution report
- `metric_name`: identifies the requested calculation
- `parameters`: records effective parameter values, including defaults
- `outputs`: maps each output name to its scope, schema, and provider ownership

With `AsyncLyraClient`, use `await client.results.describe(job_id)`. Both clients return complete metadata, automatically downloading descriptions that exceed the inspection limit.

You can still read the description after queue expiry or plugin changes while the store retains the result. Editing the returned model leaves the saved metadata intact.

### Inspect a description over HTTP

Send `GET /results/{job_id}` with your agent Bearer credential to inspect the description as JSON. Both response variants return HTTP `200`:

| `kind` | Response contents |
| --- | --- |
| `complete` | The complete metadata in `description` |
| `preview` | `job_id`, `omitted=["description"]`, and an absolute `download_url` |

Lyra returns a preview when the encoded complete response exceeds 65,536 bytes. The preview omits the entire description; Lyra never trims individual schemas, parameters, or outputs.

Follow `download_url` with the same agent credential to retrieve the complete description. The URL preserves your deployment’s public path prefix and ends in `/results/{job_id}/description/download`. The download returns `application/json` without an inspection wrapper or inspection size limit.

Use the response code to handle lookup failures on either endpoint:

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `result_not_found` | `404` | No result exists for this job ID |
| `result_integrity_error` | `500` | Required metadata or relationships are damaged |

Python clients raise the corresponding [result lookup exceptions](#handle-lookup-failures). Each exception identifies the requested `job_id` and sets `output_name=None` for this whole-result lookup.

## Read a retained table

Use an output name from `description.outputs` with the saved `job_id`. Install `lyra[client,data]` to reconstruct native pandas and GeoPandas tables from Arrow:

```python
maintenance = client.results.table(job_id, "maintenance")
client.results.download_table(job_id, "maintenance", "maintenance.arrow")

print(maintenance)
```

Run these calls inside your client context. With `AsyncLyraClient`, await each call. These lookups remain available after job expiry and plugin registration changes while the retained store is available.

`table()` returns an independent DataFrame or GeoDataFrame with declared columns and nullable string, integer, float, and boolean types. Spatial tables keep active `geometry` in the native CRS, including empty and all-null outputs. Missing geometry and empty geometry remain distinct. You can use the frame after closing the client. Editing its values, columns, geometry, CRS, or attributes cannot change stored data, another read, or the separate description.

Read provider ownership from `description.outputs[name].owner_scope`. Equal location IDs from different provider scopes or revisions identify different data. Frame attributes do not establish that ownership.

### Save and open an Arrow stream

Install `lyra[client]` and create the destination directory before calling `download_table(job_id, output_name, path)`. The client streams the Arrow bytes to disk and replaces the destination when the download finishes. It returns `None`. If the download fails or you cancel it, the client removes the temporary file and preserves any existing destination.

The file always contains an Arrow IPC stream, regardless of its filename extension. With `lyra[data]`, open a nonspatial table using an ordinary PyArrow reader:

```python
import pyarrow as pa

with open("maintenance.arrow", "rb") as source:
    with pa.ipc.open_stream(source) as reader:
        maintenance = reader.read_all().to_pandas()
```

For a spatial output, use GeoPandas to restore geometry and CRS from the stream metadata:

```python
import geopandas as gpd

with open("inspection_points.arrow", "rb") as source:
    with pa.ipc.open_stream(source) as reader:
        points = gpd.GeoDataFrame.from_arrow(reader.read_all())
```

Both methods use the authenticated [Arrow download endpoint](../rest-api/#download-a-retained-table). [Lookup errors](#handle-lookup-failures) identify the job ID and output name. Transport failures, unexpected content types, and malformed table responses raise `DownloadError`.

## Read an execution report

Save `job_id` from submission. With a [connected Python client](../python-client/#connect-and-inspect-a-metric), choose how to read the report:

```python {1-3}
report = client.reports.get(job_id)
inspection = client.reports.inspect(job_id)
client.reports.download(job_id, "report.json")
```

Call `get()` for the complete [`ExecutionReport`](../../api/lyra/contracts/reports/), `inspect()` for a bounded response, or `download()` to save JSON. Use `await` for each operation with `AsyncLyraClient`.

These methods work after job expiry or plugin changes, while the retained store remains available. They don’t load output tables or rerun the calculation. You can use returned models after closing the client; editing them doesn’t change later reads.

Use the report to inspect these saved execution details:

- Your normalized request and effective parameters, including defaults and nulls
- Input and parameter bindings, provider scopes, and revisions
- Observed execution outcomes, including reuse and skipped work

### Choose an inspection or complete download

Read `inspection.kind` to decide whether you need a download. When it equals `"complete"`, read the entire report from `inspection.report`.

When the encoded report exceeds the inspection limit, Lyra returns these preview fields:

| Field | Value |
| --- | --- |
| `kind` | `"preview"` |
| `job_id` | The saved job ID |
| `metric` | The root metric’s name |
| `status` | `"succeeded"` |
| `omitted` | `["report"]` |
| `download_url` | Absolute URL for the complete report; requires your agent credential |

Large parameters and large graphs produce the same preview. Lyra omits the entire report from the inspection and preserves it in the download. Call `reports.get(job_id)` whenever you need the complete report, regardless of the inspection’s `kind`.

For HTTP access, use these endpoints relative to your deployment’s public API base URL:

| Endpoint | Response |
| --- | --- |
| `GET /executions/{job_id}/report/inspection` | Complete inspection or fixed preview, both HTTP `200`, within 65,536 encoded bytes |
| `GET /executions/{job_id}/report/download` | Complete report as `application/json`, without an inspection wrapper or size limit |

Authenticate both endpoints with your agent Bearer credential. Use the same credential when following `download_url`; the URL includes your deployment’s public path prefix.

Create the destination’s parent directory before calling `reports.download()`. The method returns `None` and replaces an existing file only after the download finishes and its resources close successfully. If the download fails or you cancel it, the client removes the temporary file and preserves your original destination.

### Handle report lookup failures

Check the error code when either HTTP endpoint fails. Each error identifies the requested `job_id`:

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `report_not_found` | `404` | No retained report matches the job ID |
| `report_integrity_error` | `500` | The retained report or its required relationships are damaged |

Python raises the corresponding [lookup exception](#handle-lookup-failures). A missing required final result description causes an integrity error. Reports remain valid when they describe intermediate results that execution didn’t need to store.

## Inspect before downloading

Use `client.results.inspect(job_id)` or `GET /jobs/{job_id}/result/inspection` to read summaries without downloading the complete tables. For a successful job, inspection includes:

- Output declarations and row counts
- The durable `job_id`
- Numeric summaries and preview rows
- Compact [provenance](../../concepts/provenance/): a summary of the request and execution identity
- The remaining result lifetime

Use the relative path in `download` to fetch the full result. Truncation fields describe what inspection omits. The saved report stays complete, and both HTTP and Model Context Protocol (MCP) inspections include the job ID.

Both clients validate that the job ID contains exactly 32 lowercase hexadecimal characters before sending a request.

For HTTP requests, use the links from the submission response or insert the job ID directly into the route.

## Reopen a local result

Keep these values together after a [local run](../local-execution/):

| Value | Purpose |
| --- | --- |
| `result.metadata.store_path` | Resolved path to the SQLite file |
| `result.metadata.job_id` | Identifies that execution’s saved final result |

Install `lyra[runtime]` and the [city plugin](../local-execution/#load-an-installed-plugin) to use the bundled street data. Save the following snippets as `reopen_result.py`. Run the same request twice and keep the first run’s store path and job ID.

```python title="reopen_result.py" {7-9}
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
inputs = {"streets": {"provider": "city_streets", "ids": ["street-1"]}}
first = runner.run("city_street_maintenance", inputs=inputs)
second = runner.run("city_street_maintenance", inputs=inputs)
store_path = first.metadata.store_path
job_id = first.metadata.job_id
assert job_id != second.metadata.job_id
```

Each run receives a fresh job ID, even when it reuses stored values. Open the store read-only to retrieve the report, all outputs, or one named table:

```python title="reopen_result.py" {4-7}
from lyra.runtime.results import ResultStore

with ResultStore.open(store_path) as store:
    description = store.describe_result(job_id)
    report = store.read_report(job_id)
    restored = store.read_result(job_id)
    maintenance = store.read_table(job_id, "maintenance")

print(maintenance)
print(restored.metadata.parameters)
assert restored.metadata.job_id == job_id
assert restored.report == report
assert description.outputs == restored.metadata.outputs
```

Run `uv run python reopen_result.py`. To reopen the result in another process, supply the saved path and job ID. That process needs `lyra[runtime]` and access to the SQLite file.

Choose the read method for the data you need:

- `describe_result()` (also `describe()`): complete `ResultDescription`, with no inspection limit or table or report-body reads
- `read_report()`: complete `ExecutionReport`, without loading output tables
- `read_result()`: all named tables, metadata, and the complete report attached as `.report`
- `read_table()`: one named output DataFrame or GeoDataFrame
- `read_location_input()`: one named root location input as an independent GeoDataFrame

The metadata describes the metric, effective parameters, output schemas and ownership, and saved addresses. Table-reading methods reconstruct declared types and the CRS, including empty and all-null columns.

Leaving the context closes the reader’s connections, but you can still use the returned values. Editing them, including nested report fields, doesn’t change saved values or later reads. Field access doesn’t copy them again.

Reading through a closed reader raises `RuntimeError`. Opening a missing file raises `FileNotFoundError` without creating a store.

Two jobs can reuse the same rows and still have different reports. Read the [execution observations](#inspect-execution-provenance) to see which handlers ran.

### Handle lookup failures

Import these exceptions from `lyra.contracts.errors`:

| Exception | Meaning |
| --- | --- |
| `ResultNotFoundError` | The store has no result for that job ID or no output with that name |
| `ResultIntegrityError` | Required retained data or relationships are damaged |

Both result exceptions include `job_id` and `output_name`. For a whole-result read, `output_name` is `None`. Report lookups also identify the job in each exception.

| Exception | Meaning |
| --- | --- |
| `ReportNotFoundError` | A valid job ID has no retained report in this store |
| `ReportIntegrityError` | The retained report or its required relationships are damaged |

All four exceptions inherit from `ResultAccessError`. Use their attributes to identify the job and, for table lookups, the selected output.

If a saved result lacks its required report, reading the result raises `ResultIntegrityError`. If no run published a report for the job ID, reading the report raises `ReportNotFoundError`.

Lookup doesn’t repair missing data or rerun a calculation. Reports can describe intermediates that execution didn’t need to store. Report-only reads check descriptive relationships without loading every stored table; table reads check the data they need.

## Read retained location inputs

Choose an input from `description.location_inputs`. It lists each named `LocationDependency` of the requested metric, including reference inputs and inputs from operations. Read input tables separately from the metric outputs in `RunResult.tables`.

When you read or export an available input, Lyra returns the saved selection, including any added records the calculation selected. Retrieval uses the saved values without querying the provider again. To see every submitted change, including additions outside the selection and all removal IDs, read `report.request.interventions`.

For the city result above, inspect and read the `streets` input:

```python title="reopen_result.py"
with ResultStore.open(store_path) as store:
    input_description = store.describe_result(job_id).location_inputs["streets"]
    streets = store.read_location_input(job_id, "streets")

print(input_description.provider_scope)
print(streets.geometry)
```

Inspect the description for column units and provider scopes, including revisions and origin descriptions. Read the GeoDataFrame for the saved rows, nullable types, and CRS. Both remain available after queue expiry or plugin changes. Editing either leaves the stored data intact.

An input includes its complete selection. If the run reuses A and B and computes C, the primary input contains all three. A shared reference selection appears once.

For a correspondence, each (`source_location_id`, `location_id`) pair has the target’s attributes and geometry. A target selected by two sources appears twice. For a route from A through P to T, the input retains the final P to T pairs.

Read `source_scope` for P and `provider_scope` for T. The scope distinguishes records with the same ID across providers or revisions. Input tables have no metric `row_key` or output ownership.

Check `availability` before reading:

| State | Read behavior |
| --- | --- |
| `available` | Returns the complete input, including a typed empty frame for a known empty selection or completed zero-match operation |
| `not_materialized` | Raises `LocationInputUnavailableError`; execution skipped work without a complete retained binding |

An empty output can have nonempty inputs. If the run skips a metric with an empty primary input, its independent reference input may be unavailable. Later runs preserve that published state.

Missing data for an available input raises `ResultIntegrityError`. An unknown input name raises `ResultNotFoundError`. Both errors include `job_id`, `input_name`, and `output_name=None`.

### Read the same input remotely

Choose an `input_name` from the saved description. With the same job ID, you can read the input as a GeoDataFrame, inspect a page, or download Arrow bytes.

```python title="read_location_input.py"
description = client.results.describe(job_id)
input_description = description.location_inputs[input_name]
frame = client.results.location_input(job_id, input_name)
page = client.results.location_input_rows(job_id, input_name)
client.results.download_location_input(job_id, input_name, "input.arrow")
```

With `AsyncLyraClient`, await each call. Downloads accept a string or `Path` and return `None`. Create the parent directory first. The client replaces the destination only after a successful download.

The Arrow stream preserves nullable types, CRS, and GeoArrow WKB geometry. Even an empty input retains the declared schema.

Input pages use the same [JSON encodings and byte limits](#read-paginated-rows) as output pages. Pass each `next_cursor` with the same job ID and input name until it is `None`. Output cursors apply only to output tables. An oversized input row raises `RowTooLargeError` with an authenticated Arrow download URL.

| Access | Description or input operation |
| --- | --- |
| HTTP description | `GET /results/{job_id}` |
| HTTP description download | `GET /results/{job_id}/description/download` |
| HTTP rows | `GET /results/{job_id}/location-inputs/{input_name}/rows` |
| HTTP Arrow | `GET /results/{job_id}/location-inputs/{input_name}/download` |
| MCP description | `lyra_describe_result(job_id)` |
| MCP rows | `lyra_get_location_input_rows(job_id, input_name)` |
| MCP Arrow link | `lyra_download_location_input(job_id, input_name)` |

Use your agent Bearer credential for HTTP reads and downloads. Arrow responses use `application/vnd.apache.arrow.stream`. If a description exceeds the HTTP or MCP size limit, the preview includes `omitted=["description"]` and a download URL. Both Python clients fetch the complete JSON automatically.

For error codes, see the [REST guide](../rest-api/) or [MCP guide](../mcp/).

## Retrieve tables in Python

Install `lyra[client,tables]` to retrieve remote tables. Pass the job ID to your connected client:

```python
tables = client.result_tables(job_id)
table = tables["maintenance"]
print(table.head())
```

`client.results.tables(job_id)` is equivalent. Both methods return a dictionary, including for a single output. Await these methods when using an async client; failed jobs raise `MetricRunError`.

Ordinary outputs become pandas DataFrames; spatial outputs become GeoPandas GeoDataFrames. The client reconstructs logical dtypes and CRS from saved output descriptions, including for empty tables.

Returned tables have fresh positional indices. Align rows with declared identifier columns and sort explicitly for presentation; storage and retrieval promise no implicit row order.

## Save the complete JSON result

Save a successful job’s complete result to a local file with the download method:

```python
client.results.download(job_id, "result.json")
```

The HTTP equivalent is `GET /jobs/{job_id}/result/download`, using the agent Bearer credential. The client checks the HTTP response status before opening the destination file.

The successful download contains the same full JSON object as `GET /jobs/{job_id}/result`:

| Field | Meaning |
| --- | --- |
| `job_id` | Job ID from submission |
| `outputs` | Output names mapped to record arrays or GeoJSON FeatureCollections |
| `output_metadata` | The declarations that describe those outputs |
| `provenance` | The accepted request and transient execution observations |

Empty ordinary outputs are empty arrays. Empty spatial outputs have an empty `features` array. Missing cells become null. Temporal and categorical concepts use the strings or numbers declared by the plugin.

## Inspect execution provenance

Read full `provenance` from the result or download. Use these fields to compare your request with the execution:

| Field | What to inspect |
| --- | --- |
| `provenance.accepted.plan.request` | Your validated location selections and the requested metric’s parameters after defaults and validation |
| `provenance.accepted.plan.request.parameters` | Effective parameter values, including defaults |
| `provenance.execution` | Resolved input summaries and node outcomes from the execution attempt; `null` if execution has not started |

A planned dependency can reuse a cached result without running its handler. Read [execution reports and provenance](../../concepts/provenance/) to interpret reuse and failures. For local success, inspect `result.report`; use `runner.inspect()` for diagnostics from the latest attempt.

Use `client.results.describe(job_id)` for saved output metadata or `client.reports.get(job_id)` for the complete report. Both methods accept the job ID you received at submission.

## Understand row identity

When joining results to other data, use the producing plugin’s identifier columns. Its row counts and identifiers can differ from the features in your original request.

[Location-scoped outputs](../../plugins/outputs/#declare-location-ownership) use `(location_id, row_key)` within their owning provider scope. Unpartitioned outputs can contain equal-valued duplicate rows. Store any meaningful sequence in an explicit column.

[Canonical selection](../../concepts/selection/) preserves what each output row represents. For example, a tract metric selected through a state still returns tract rows. Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

Job JSON endpoints return spatial tables as GeoJSON in the World Geodetic System 1984 (WGS84). The Python client’s job-table methods restore declared types and reproject geometries to the saved CRS. Returned tables use fresh positional indices.

Use `client.results.table(job_id, output_name)` to read native coordinates directly, or [save and open an Arrow stream](#save-and-open-an-arrow-stream). Empty outputs retain their schema and CRS.

For an AI assistant, use [`lyra_download_table`](../mcp/#download-a-retained-table) to get the table’s download address. Fetch that address with your agent Bearer credential.

Request provenance records your submitted ID selections. The provider declares the CRS it uses during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Keep access after job expiry

Save the deployment address and `job_id` at submission. Once the queue record expires, job status and job-result downloads expire with it.

To [reconnect with the job ID](../python-client/#reconnect-with-the-job-id), use `client.results.describe(job_id)` for metadata. Pass the same ID and an output name to `client.results.table()` or `client.results.download_table()`. These methods read the [shared SQLite store](../../operate/caching/).

With access to the SQLite file, you can also [reopen the report and tables locally](#reopen-a-local-result). Use the same job ID for each lookup.

Changed keys and cache policies don’t delete saved records. Retention applies within a [supported store](../../operate/caching/#preserve-stored-results); incompatible schema changes can require an explicit development-store reset. A reset removes the store’s contents.

A job-status lookup returns HTTP 404 for both unknown and expired jobs. A job-result lookup returns HTTP 500 with `invalid_job_result` if required stored data is corrupt, inconsistent, or missing.

Repeated downloads cannot recover missing retained data. A new submission creates another job, which may reuse cached calculations.

MCP previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.

## Read paginated rows

Use your saved `job_id` and an `output_name` from `description.outputs` to read rows in JSON pages. This loop prints every row from that output:

```python title="iterate_rows.py" {3,6-8}
cursor = None
while True:
    page = client.results.rows(job_id, output_name, cursor=cursor)
    for row in page.rows:
        print(row)
    cursor = page.next_cursor
    if cursor is None:
        break
```

With `AsyncLyraClient`, await each call to `client.results.rows()`. Pass `next_cursor` back unchanged with the same job ID and output name. Continue after a short page and stop when `next_cursor` is `None`.

Set `limit` to a positive integer to change the default of 100 rows per page. Each encoded response fits within 65,536 bytes, including the MCP response envelope. Duplicate rows remain separate entries across pages.

Interpret cells using these JSON encodings:

- **Integers**: decimal strings to preserve precision when you parse the response
- **Floating-point values**: JSON numbers
- **Booleans**: JSON booleans
- **Missing cells**: `null`
- **Strings**: unchanged text, including whitespace
- **Geometry**: WKT in the saved CRS, including empty geometries

Read schemas and provider ownership with [`results.describe()`](#read-a-retained-description); row pages contain only declared public columns. Use [`results.table()`](#read-a-retained-table) for native nullable scalar types and geometry objects.

### Recover a row that exceeds the size limit

If a row cannot fit in an empty page, the client raises `RowTooLargeError`. The exception identifies the result and output through `job_id` and `output_name`, and includes an absolute Arrow `download_url`.

Lyra returns any preceding rows that fit in a page, along with a continuation cursor. Requesting that continuation raises the error without skipping the oversized row.

Download the complete output, including the oversized row, as an [Arrow stream](#save-and-open-an-arrow-stream):

```python title="recover_rows.py" {6,12-13}
from lyra.contracts.errors import RowTooLargeError

cursor = None
try:
    while True:
        page = client.results.rows(job_id, output_name, cursor=cursor)
        for row in page.rows:
            print(row)
        cursor = page.next_cursor
        if cursor is None:
            break
except RowTooLargeError:
    client.results.download_table(job_id, output_name, "complete.arrow")
```

Set `output_name` to a declared name and run this loop inside your client context. With the async client, await `rows()` and `download_table()`.

The file `complete.arrow` contains the whole output, including rows you already printed. Process it separately to avoid counting those rows twice.

You can also fetch the exception’s `download_url` with your agent Bearer credential. Both download methods remain available after job expiry.

For invalid limits or cursors, the client raises `InvalidPaginationError`. Check its `parameter` attribute to identify whether you need to correct `limit` or `cursor`.

### Read row pages over HTTP or MCP

Send `GET /results/{job_id}/tables/{output_name}/rows` with your agent Bearer credential. Set `limit` to choose the page size. Omit `cursor` for the first page. On success, the API returns HTTP 200 with `rows` and `next_cursor`.

With [MCP](../mcp/), call `lyra_get_result_rows` with `job_id` and `output_name`. Read `rows` and `next_cursor` from `structuredContent`. MCP uses the same pagination arguments and cell encodings as HTTP.

## Export retained spatial selections

Export a retained spatial output or location input through `ResultStore` or `client.results`. Choose the method for your selection:

- `export_geoparquet(job_id, output_name, path)` for a named spatial output
- `export_location_input_geoparquet(job_id, input_name, path)` for a direct provider or correspondence input

Await either method with the async client. Known empty spatial selections retain their schema and scopes. Unavailable inputs raise `LocationInputUnavailableError`; nonspatial outputs raise `ValueError`.

See [export spatial tables to GIS files](../location-exports/) for examples using local destinations and retained originals. The guide also covers GeoPandas writes for edited frames and other formats.
