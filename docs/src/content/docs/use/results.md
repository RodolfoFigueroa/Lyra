---
title: Read and download results
description: Read saved metadata and execution reports, download remote tables, and reopen local results.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

Read saved metadata with `result_ref` and [execution reports](../../concepts/provenance/) with `execution_id`, even after a job expires. Use the Python client for remote access or `ResultStore` to open a local store. Follow this guide to choose a lookup and handle missing data.

## Read a retained description

Save the `result_ref` from a successful job response. With a [connected Python client](../python-client/#connect-and-inspect-a-metric), use that reference to read metadata without loading tables or the execution report:

```python {1}
description = client.results.describe(result_ref)
print(description.metric_name, description.parameters)
for name, output in description.outputs.items():
    print(name, output.table_schema, output.owner_scope)
```

Read these fields on the returned [`ResultDescription`](../../api/lyra/contracts/results/):

- `result_ref` and `execution_id`: identify the saved result and its execution report
- `metric_name`: identifies the requested calculation
- `parameters`: records effective parameter values, including defaults
- `outputs`: maps each output name to its scope, schema, and provider ownership

With `AsyncLyraClient`, use `await client.results.describe(result_ref)`. Both clients return complete metadata, automatically downloading descriptions that exceed the inspection limit.

Keep the reference for later retrieval, even after job expiry or plugin registration changes. Retrieval requires the retained store to remain available. You can use returned models after closing the client; editing nested fields doesn’t change saved metadata or later reads.

### Inspect a description over HTTP

Send `GET /results/{result_ref}` with your agent Bearer credential to inspect the description as JavaScript Object Notation (JSON). Both response variants return HTTP `200`:

| `kind` | Response contents |
| --- | --- |
| `complete` | The complete metadata in `description` |
| `preview` | `result_ref`, `execution_id`, `omitted=["description"]`, and an absolute `download_url` |

Lyra returns a preview when the encoded complete response exceeds 65,536 bytes. The preview omits the entire description; Lyra never trims individual schemas, parameters, or outputs.

Follow `download_url` with the same agent credential to retrieve the complete description. The URL preserves your deployment’s public path prefix and ends in `/results/{result_ref}/description/download`. The download returns `application/json` without an inspection wrapper or inspection size limit.

Use the response code to handle lookup failures on either endpoint:

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `invalid_result_reference` | `422` | The reference is malformed |
| `result_not_found` | `404` | No retained result matches the reference |
| `result_integrity_error` | `500` | Required metadata or relationships are damaged |

Python clients raise the corresponding [result lookup exceptions](#handle-lookup-failures). Each exception identifies the requested `result_ref` and sets `output_name=None` for this whole-result lookup.

## Read a retained table

Use an output name from `description.outputs` with the saved `result_ref`. Install `lyra[client,data]` to reconstruct native pandas and GeoPandas tables from Arrow:

```python
with LyraClient("lyra.example.com", agent_api_key="your_agent_key_here") as client:
    description = client.results.describe(result_ref)
    maintenance = client.results.table(result_ref, "maintenance")
    client.results.download_table(result_ref, "maintenance", "maintenance.arrow")

print(maintenance)
```

Import `LyraClient` from `lyra.api`. Use `await` for both table methods with `AsyncLyraClient`. These lookups remain available after job expiry and plugin registration changes while the retained store is available.

`table()` returns an independent DataFrame or GeoDataFrame with declared columns and nullable string, integer, float, and boolean types. Spatial tables keep active `geometry` in the native CRS, including empty and all-null outputs. Missing geometry and empty geometry remain distinct. You can use the frame after closing the client. Editing its values, columns, geometry, CRS, or attributes cannot change stored data, another read, or the separate description.

Read provider ownership from `description.outputs[name].owner_scope`. Equal location IDs from different provider scopes or revisions identify different data. Frame attributes do not establish that ownership.

### Save and open an Arrow stream

`download_table(result_ref, output_name, path)` needs only `lyra[client]` and streams the exact Arrow bytes to disk. The method returns `None`. Create the parent directory first; successful completion replaces an existing destination after closing the file and response. Failure or cancellation removes the temporary file and leaves the destination unchanged, or absent if it did not exist.

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

Both client methods use the authenticated [Arrow download endpoint](../rest-api/#download-a-retained-table). Structured failures raise the [result lookup exceptions](#handle-lookup-failures), carrying `result_ref` and `output_name`. Transport failures, unexpected content types, and malformed table responses raise `DownloadError`; a failed table read returns no partial frame.

## Read an execution report

Save `execution_id` from a successful job response. With a [connected Python client](../python-client/#connect-and-inspect-a-metric), choose how to read the report:

```python {1-3}
report = client.reports.get(execution_id)
inspection = client.reports.inspect(execution_id)
client.reports.download(execution_id, "report.json")
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
| `execution_id` | The saved execution ID |
| `metric` | The root metric’s name |
| `status` | `"succeeded"` |
| `omitted` | `["report"]` |
| `download_url` | Absolute URL for the complete report; requires your agent credential |

Large parameters and large graphs produce the same preview. Lyra omits the entire report from the inspection and preserves it in the download. Call `reports.get(execution_id)` whenever you need the complete report, regardless of the inspection’s `kind`.

For HTTP access, use these endpoints relative to your deployment’s public API base URL:

| Endpoint | Response |
| --- | --- |
| `GET /executions/{execution_id}/report/inspection` | Complete inspection or fixed preview, both HTTP `200`, within 65,536 encoded bytes |
| `GET /executions/{execution_id}/report/download` | Complete report as `application/json`, without an inspection wrapper or size limit |

Authenticate both endpoints with your agent Bearer credential. Use the same credential when following `download_url`; the URL includes your deployment’s public path prefix.

Create the destination’s parent directory before calling `reports.download()`. The method returns `None` and replaces an existing file only after the download finishes and its resources close successfully. If the download fails or you cancel it, the client removes the temporary file and preserves your original destination.

### Handle report lookup failures

Check the error code when either HTTP endpoint fails. Each error identifies the requested `execution_id`:

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `invalid_execution_id` | `422` | The execution ID is malformed |
| `report_not_found` | `404` | No retained report matches the execution ID |
| `report_integrity_error` | `500` | The retained report or its required relationships are damaged |

Python raises the corresponding [lookup exception](#handle-lookup-failures). A missing required final result description causes an integrity error. Reports remain valid when they describe intermediate results that execution didn’t need to store.

## Inspect before downloading

Use `client.results.inspect(job_id)` or `GET /jobs/{job_id}/result/inspection` to read summaries without downloading the complete tables. For a successful job, inspection includes:

- Output declarations and row counts
- The durable `execution_id` and `result_ref`
- Numeric summaries and preview rows
- Compact [provenance](../../concepts/provenance/): a summary of the request and execution identity
- The remaining result lifetime

The `download` field gives the relative path to the full result. Truncation fields record what inspection omits; these limits never truncate the saved execution report. Successful HTTP and Model Context Protocol (MCP) inspections include `execution_id` and `result_ref`, even when Lyra omits optional details.

Pass `job_id` as a nonempty string. The client preserves whitespace and treats the entire string as the job ID. Empty strings and other types raise a validation error before the client sends requests or opens files.

Use the returned links or Python client to access a job. When building a request path yourself, percent-encode the ID as one path component: `/` becomes `%2F`. Encode the IDs `.` and `..` as `%2E` and `%2E%2E` to prevent clients from changing the path.

## Reopen a local result

Keep these values together after a [local run](../local-execution/):

| Value | Purpose |
| --- | --- |
| `result.metadata.store_path` | Resolved path to the SQLite file |
| `result.metadata.execution_id` | Identifies the complete execution report in that store |
| `result.metadata.result_ref` | Identifies that execution’s saved final result |

Pass each ID or reference unchanged to its lookup method; don’t parse or reconstruct it. These identifiers differ from remote job IDs. The attached `result.report` has matching `execution_id` and `result_ref` values.

Install `lyra[runtime]` and the [city plugin](../local-execution/#load-an-installed-plugin) to run this example with its bundled data. Save both snippets below as `reopen_result.py`. First, run the same request twice and retain the first result’s address:

```python title="reopen_result.py" {7-11}
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
inputs = {"streets": {"provider": "city_streets", "ids": ["street-1"]}}
first = runner.run("city_street_maintenance", inputs=inputs)
second = runner.run("city_street_maintenance", inputs=inputs)
store_path = first.metadata.store_path
execution_id = first.metadata.execution_id
result_ref = first.metadata.result_ref
assert execution_id != second.metadata.execution_id
assert result_ref != second.metadata.result_ref
```

Every successful run receives a distinct execution ID and result reference, even when it reuses stored values. Open the store read-only to retrieve the report, all outputs, or one named table:

```python title="reopen_result.py" {3-6}
from lyra.runtime.results import ResultStore

with ResultStore.open(store_path) as store:
    description = store.describe(result_ref)
    report = store.read_report(execution_id)
    restored = store.read_result(report.result_ref)
    maintenance = store.read_table(result_ref, "maintenance")

print(maintenance)
print(restored.metadata.parameters)
assert restored.metadata.result_ref == result_ref
assert restored.metadata.execution_id == execution_id
assert restored.report == report
assert description.outputs == restored.metadata.outputs
```

Run `uv run python reopen_result.py`. To read in another process, reuse the second snippet with the identifiers you saved. That process needs `lyra[runtime]` and access to the store file, but no plugins or external services. If you saved only the path and result reference, `read_result(result_ref)` also returns the matching report.

Choose the read method for the data you need:

- `describe()`: complete `ResultDescription`, with no inspection limit or table or report-body reads
- `read_report()`: complete `ExecutionReport`, without loading output tables
- `read_result()`: all named tables, metadata, and the complete report attached as `.report`
- `read_table()`: one named DataFrame or GeoDataFrame

The metadata describes the metric, effective parameters, output schemas and ownership, and saved addresses. Both table-reading methods reconstruct declared types and the coordinate reference system (CRS), including empty and all-null columns.

Leaving the context closes the reader’s connections, but you can still use the returned values. Editing them, including nested report fields, doesn’t change saved values or later reads. Field access doesn’t copy them again.

Reading through a closed reader raises `RuntimeError`. Opening a missing file raises `FileNotFoundError` without creating a store.

Development runs, empty results, and cache hits also receive distinct references. Separate references can select the same immutable values without copying rows. Use [execution observations](#inspect-execution-provenance) to check whether a handler ran; a result reference identifies saved output access.

### Handle lookup failures

Import these exceptions from `lyra.contracts.errors`:

| Exception | Meaning |
| --- | --- |
| `InvalidResultReferenceError` | The supplied reference is malformed |
| `ResultNotFoundError` | A well-formed reference has no retained result, or the output name is undeclared |
| `ResultIntegrityError` | Required retained data or relationships are damaged |

These result exceptions expose `result_ref` and `output_name`; whole-result reads set `output_name=None`. Report lookup has corresponding categories:

| Exception | Meaning |
| --- | --- |
| `InvalidExecutionIDError` | The supplied execution ID is malformed |
| `ReportNotFoundError` | A valid execution ID has no retained report in this store |
| `ReportIntegrityError` | The retained report or its required relationships are damaged |

Report exceptions expose `execution_id`, and all six lookup exceptions inherit from `ResultAccessError`. Inspect these fields instead of parsing messages.

The exception follows the lookup you requested. Reading an existing result with a missing required report raises `ResultIntegrityError`. Looking up a valid execution ID without a saved report raises `ReportNotFoundError`, including after a failed attempt.

Lookup doesn’t repair missing data or rerun a calculation. Reports can describe intermediates that execution didn’t need to store. Report-only reads check descriptive relationships without loading every stored table; table reads check the data they need.

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
| `execution_id` | Durable execution identity for the retained report |
| `result_ref` | Durable reference for the retained final result |
| `outputs` | Output names mapped to record arrays or GeoJSON FeatureCollections |
| `output_metadata` | The declarations that describe those outputs |
| `provenance` | The accepted plan and transient execution observations |

Empty ordinary outputs are empty arrays. Empty spatial outputs have an empty `features` array. Missing cells become null. Temporal and categorical concepts use the strings or numbers declared by the plugin.

## Inspect execution provenance

Read full `provenance` from the result or download. Use these fields to compare your request with the execution:

| Field | What to inspect |
| --- | --- |
| `provenance.accepted.plan.request` | Your validated location selections and the requested metric’s parameters after defaults and validation |
| `provenance.accepted.plan.parameters` | Effective parameter values, including defaults |
| `provenance.execution` | Resolved input summaries and node outcomes from the execution attempt; `null` if execution has not started |

A planned dependency can reuse a cached result without running its handler. Read [execution reports and provenance](../../concepts/provenance/) to interpret reuse and failures. For local success, inspect `result.report`; use `runner.inspect()` for diagnostics from the latest attempt.

Save `.execution_id` and `.result_ref` from a successful job response for later lookups. Use `results.describe(result_ref)` for retained metadata. Use `reports.get(execution_id)` for the complete report, or `ResultStore` with access to the store file.

## Understand row identity

When joining results to other data, use the producing plugin’s identifier columns. Its row counts and identifiers can differ from the features in your original request.

[Location-scoped outputs](../../plugins/outputs/#declare-location-ownership) use `(location_id, row_key)` within their owning provider scope. Unpartitioned outputs can contain equal-valued duplicate rows. Store any meaningful sequence in an explicit column.

[Canonical selection](../../concepts/selection/) preserves what each output row represents. For example, a tract metric selected through a state still returns tract rows. Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

Job JSON endpoints return spatial tables as GeoJSON in the World Geodetic System 1984 (WGS84). The Python client’s job-table methods restore declared types and reproject geometries to the saved CRS. Returned tables use fresh positional indices.

Use `client.results.table(result_ref, output_name)` to read native coordinates directly, or [save and open an Arrow stream](#save-and-open-an-arrow-stream). Empty outputs retain their schema and CRS.

For an AI assistant, use [`lyra_download_table`](../mcp/#download-a-retained-table) to get the table’s download address. Fetch that address with your agent Bearer credential.

Request provenance records your submitted ID selections. The provider declares the CRS it uses during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Keep access after job expiry

Save `result_ref` and `execution_id` before the inspection’s remaining job lifetime reaches zero. The operator’s retention setting controls how long you can access completed jobs through job-based methods. A job ID does not extend this period. [Reconnect with the durable identifiers](../python-client/#reconnect-with-durable-identifiers) to read descriptions, tables, rows, and reports later.

The server retains result data indefinitely in the [shared SQLite store](../../operate/caching/). Job expiration ends access by job ID without deleting those records. Keep using `results.describe(result_ref)`, `results.table(result_ref, output_name)`, and `results.download_table(result_ref, output_name, path)` after the job expires.

If you have filesystem access to the store, follow the [reopening example](#reopen-a-local-result) with your saved execution ID. Read the report, then use its `result_ref` to read the result. This access survives job expiry and needs no job record or plugin. Remote responses don’t grant access to the server’s filesystem.

Changed keys and cache policies don’t delete saved records. Retention applies within a [supported store](../../operate/caching/#preserve-stored-results); incompatible schema changes can require an explicit development-store reset. A reset removes the store’s contents.

Missing or expired jobs return HTTP `404`. This response does not distinguish a job that never existed from one whose retention period ended. If required stored data is missing, corrupt, or inconsistent, Lyra returns HTTP `500` with code `invalid_job_result`.

Repeated downloads cannot recover missing retained data. A new submission creates another job, which may reuse cached calculations.

MCP previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.

## Read paginated rows

Use your saved `result_ref` and an `output_name` from `description.outputs` to read rows in JSON pages. This loop prints every row from that output:

```python title="iterate_rows.py" {3,6-8}
cursor = None
while True:
    page = client.results.rows(result_ref, output_name, cursor=cursor)
    for row in page.rows:
        print(row)
    cursor = page.next_cursor
    if cursor is None:
        break
```

With `AsyncLyraClient`, use `await client.results.rows(result_ref, "area", cursor=cursor)`. The `next_cursor` value identifies where to resume reading; pass it unchanged with the same result and output. Continue until `next_cursor` is `None`, even when a page contains fewer rows than requested.

Set `limit` to a positive integer to change the default of 100 rows per page. Each encoded response fits within 65,536 bytes, including the MCP response envelope. Duplicate rows remain separate entries across pages.

Interpret cells using these JSON encodings:

- **Integers**: decimal strings to preserve precision when you parse the response
- **Floating-point values**: JSON numbers
- **Booleans**: JSON booleans
- **Missing cells**: `null`
- **Strings**: unchanged text, including whitespace
- **Geometry**: well-known text (WKT) in the saved CRS, including empty geometries

Read schemas and provider ownership with [`results.describe()`](#read-a-retained-description); row pages contain only declared public columns. Use [`results.table()`](#read-a-retained-table) for native nullable scalar types and geometry objects.

### Recover a row that exceeds the size limit

If a row cannot fit in an empty page, the client raises `RowTooLargeError`. The exception identifies the result and output through `result_ref` and `output_name`, and includes an absolute Arrow `download_url`.

Lyra returns any preceding rows that fit in a page, along with a continuation cursor. Requesting that continuation raises the error without skipping the oversized row.

Download the complete output, including the oversized row, as an [Arrow stream](#save-and-open-an-arrow-stream):

```python title="recover_rows.py" {5,11-15}
from lyra.contracts.errors import RowTooLargeError

cursor = None
try:
    while True:
        page = client.results.rows(result_ref, output_name, cursor=cursor)
        for row in page.rows:
            print(row)
        cursor = page.next_cursor
        if cursor is None:
            break
except RowTooLargeError as error:
    client.results.download_table(error.result_ref, error.output_name, "complete.arrow")
```

Set `output_name` to a declared name and run this loop inside your client context. With the async client, await `rows()` and `download_table()`.

The file `complete.arrow` contains the whole output, including rows you already printed. Process it separately to avoid counting those rows twice.

You can also fetch the exception’s `download_url` with your agent Bearer credential. Both download methods remain available after job expiry.

For invalid limits or cursors, the client raises `InvalidPaginationError`. Check its `parameter` attribute to identify whether you need to correct `limit` or `cursor`.

### Read row pages over HTTP or MCP

Send `GET /results/{result_ref}/tables/{output_name}/rows` with your agent Bearer credential. Set the optional `limit` query parameter to control the row count; omit `cursor` for the first page. Successful responses use HTTP `200` and contain `rows` and `next_cursor`.

With [MCP](../mcp/), call `lyra_get_result_rows` with `result_ref` and `output_name`. Set optional `limit` and `cursor` arguments to control pagination. Read `rows` and `next_cursor` directly from `structuredContent`; both transports use the cell encodings and continuation rules above.
