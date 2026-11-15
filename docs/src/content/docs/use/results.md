---
title: Read and download results
description: Reopen retained results and execution reports, and retrieve remote tables.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

Use `ResultStore` to reopen saved results and [execution reports](../../concepts/provenance/), which describe your request and the work Lyra observed. For remote results, use a job ID with the Python client. This guide covers both access paths and their lookup errors.

## Inspect before downloading

Use `client.results.inspect(job_id)` or `GET /jobs/{job_id}/result/inspection` to read summaries without downloading the complete tables. For a successful job, inspection includes:

- Output declarations and row counts
- The durable `execution_id`
- Numeric summaries and preview rows
- Compact [provenance](../../concepts/provenance/): a summary of the request and execution identity
- The remaining result lifetime

The `download` field gives the relative path to the full result. Truncation fields record what inspection omits; these limits never truncate the saved execution report. Successful HTTP and Model Context Protocol (MCP) inspections include `execution_id`, even when Lyra omits optional details.

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
    report = store.read_report(execution_id)
    restored = store.read_result(report.result_ref)
    maintenance = store.read_table(result_ref, "maintenance")

print(maintenance)
print(restored.metadata.parameters)
assert restored.metadata.result_ref == result_ref
assert restored.metadata.execution_id == execution_id
assert restored.report == report
```

Run `uv run python reopen_result.py`. To read in another process, reuse the second snippet with the identifiers you saved. That process needs `lyra[runtime]` and access to the store file, but no plugins or external services. If you saved only the path and result reference, `read_result(result_ref)` also returns the matching report.

Choose the read method for the data you need:

- `read_report()`: complete `ExecutionReport`, without loading output tables
- `read_result()`: all named tables, metadata, and the complete report attached as `.report`
- `read_table()`: one named DataFrame or GeoDataFrame

The metadata describes the metric, effective parameters, output schemas and ownership, and saved addresses. Both table-reading methods reconstruct declared types and CRS, including empty and all-null columns.

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

Read `.execution_id` on the object returned by `client.results.get(job_id)`. JSON results and downloads include `execution_id` at the top level. Remote methods accept job IDs; they don’t offer complete report lookup or report downloads. To read a complete report, use `ResultStore` with filesystem access to the relevant store.

## Understand row identity

When joining results to other data, use the producing plugin’s identifier columns. Its row counts and identifiers can differ from the features in your original request.

[Location-scoped outputs](../../plugins/outputs/#declare-location-ownership) use `(location_id, row_key)` within their owning provider scope. Unpartitioned outputs can contain equal-valued duplicate rows. Store any meaningful sequence in an explicit column.

[Canonical selection](../../concepts/selection/) preserves what each output row represents. For example, a tract metric selected through a state still returns tract rows. Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

The server serializes spatial tables as GeoJSON using WGS84. Python readers restore declared dtypes and reproject GeoDataFrames to the CRS in the saved output description. Returned tables use fresh positional indices.

Request provenance records your submitted ID selections. The provider declares the CRS it uses during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Save results before retention ends

Download results you need before the inspection’s remaining lifetime reaches zero. The operator’s retention setting controls how long you can access completed jobs through job-based methods. A job ID does not extend this period.

The server retains native output rows, final result descriptions, and complete reports indefinitely in the [shared SQLite store](../../operate/caching/). Redis retains the job metadata that remote methods need. Job expiration ends access by job ID without deleting SQLite values, descriptions, or reports.

If you have filesystem access to the store, follow the [reopening example](#reopen-a-local-result) with your saved execution ID. Read the report, then use its `result_ref` to read the result. This access survives job expiry and needs no job record or plugin. Remote responses don’t grant access to the server’s filesystem.

Changed keys and cache policies don’t delete saved records. Retention applies within a [supported store](../../operate/caching/#preserve-stored-results); incompatible schema changes can require an explicit development-store reset. A reset removes the store’s contents.

Missing or expired jobs return HTTP `404`. This response does not distinguish a job that never existed from one whose retention period ended. If required stored data is missing, corrupt, or inconsistent, Lyra returns HTTP `500` with code `invalid_job_result`.

Repeated downloads cannot recover missing retained data. A new submission creates another job, which may reuse cached calculations.

MCP previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.
