---
title: Read and download results
description: Reopen local results, retrieve remote tables, and inspect execution provenance.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

Read saved local results with `ResultStore`, or retrieve a completed server job’s tables through the Python client. Each output name matches the metric’s declaration. Keep the local store path and result reference for reopening; use a job ID for remote access.

## Inspect before downloading

Use `client.results.inspect(job_id)` or `GET /jobs/{job_id}/result/inspection` to read saved summaries without loading the complete tables. For a successful job, inspection includes:

- Output declarations and row counts
- Numeric summaries and preview rows
- Compact [provenance](../../concepts/provenance/): a summary of the request and execution identity
- The remaining result lifetime

The `download` field gives the relative path to the full result. Truncation fields record what the inspection omits.

Pass `job_id` as a nonempty string. The client preserves whitespace and treats the entire string as the job ID. Empty strings and other types raise a validation error before the client sends requests or opens files.

Use the returned links or Python client to access a job. When building a request path yourself, percent-encode the ID as one path component: `/` becomes `%2F`. Encode the IDs `.` and `..` as `%2E` and `%2E%2E` to prevent clients from changing the path.

## Reopen a local result

Keep `result.metadata.store_path` and `result.metadata.result_ref` together after a [local run](../local-execution/). The path locates the SQLite file. The opaque reference identifies that execution’s saved outputs and metadata; treat it as a whole string.

Install `lyra[runtime]` and the [city plugin](../local-execution/#load-an-installed-plugin) to run this example with its bundled data. Save both snippets below as `reopen_result.py`. First, run the same request twice and retain the first result’s address:

```python title="reopen_result.py" {7-9}
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
inputs = {"streets": {"provider": "city_streets", "ids": ["street-1"]}}
first = runner.run("city_street_maintenance", inputs=inputs)
second = runner.run("city_street_maintenance", inputs=inputs)
store_path = first.metadata.store_path
result_ref = first.metadata.result_ref
assert result_ref != second.metadata.result_ref
```

Every successful run receives a distinct reference, even when it reuses stored values. Open the store read-only to retrieve all outputs or one named table:

```python title="reopen_result.py" {3-5}
from lyra.runtime.results import ResultStore

with ResultStore.open(store_path) as store:
    restored = store.read_result(result_ref)
    maintenance = store.read_table(result_ref, "maintenance")

print(maintenance)
print(restored.metadata.parameters)
assert restored.metadata.result_ref == result_ref
```

Run `uv run python reopen_result.py`. To read the result in another process, supply the saved path and reference to the second snippet. That process needs `ResultStore` from `lyra[runtime]`; it doesn’t load plugins or run handlers.

`read_result()` returns a `RunResult` containing all named DataFrames or GeoDataFrames. Its detached metadata contains:

- Metric name and complete effective parameters
- Output schemas, output scopes, and owning provider scopes
- Resolved store path and result reference

`read_table()` returns the named DataFrame or GeoDataFrame. Both methods reconstruct declared logical types and CRS, including empty and all-null columns.

Leaving the context closes the reader’s connections. Returned tables and metadata remain usable, and editing them doesn’t change stored values or later reads. Reading through the closed reader raises `RuntimeError`. Opening a missing file raises `FileNotFoundError` without creating a store.

Development runs, empty results, and cache hits also receive distinct references. Separate references can select the same immutable values without copying rows. Use [execution observations](#inspect-execution-provenance) to check whether a handler ran; a result reference identifies saved output access.

### Handle lookup failures

Import these exceptions from `lyra.contracts.errors`:

| Exception | Meaning |
| --- | --- |
| `InvalidResultReferenceError` | The supplied reference is malformed |
| `ResultNotFoundError` | A well-formed reference has no retained result, or the output name is undeclared |
| `ResultIntegrityError` | Required retained data or relationships are damaged |

All three inherit from `ResultAccessError` and expose `result_ref` and `output_name`. Whole-result reads set `output_name=None`. Inspect these fields instead of parsing error messages; lookup failures don’t rerun a calculation.

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
| `outputs` | Output names mapped to record arrays or GeoJSON FeatureCollections |
| `output_metadata` | The declarations that describe those outputs |
| `provenance` | The submitted request and execution identity |

Empty ordinary outputs are empty arrays. Empty spatial outputs have an empty `features` array. Missing cells become null. Temporal and categorical concepts use the strings or numbers declared by the plugin.

## Inspect execution provenance

Read full `provenance` from the result or download. Use these fields to compare your request with the execution:

| Field | What to inspect |
| --- | --- |
| `provenance.accepted.plan.request` | Your validated location selections and the requested metric’s parameters after defaults and validation |
| `provenance.accepted.plan.parameters` | Effective parameter values, including defaults |
| `provenance.execution` | Resolved input summaries and node outcomes from the execution attempt; `null` if execution has not started |

A planned dependency may use a cached result without running its handler. [Understand execution provenance](../../concepts/provenance/) explains how to interpret reuse and failures. For local runs, call `runner.inspect()` to read the accepted plan and execution observations.

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

The server retains native output rows and result descriptions indefinitely in the [shared SQLite store](../../operate/caching/). Redis retains the job metadata that remote methods need. Job expiration ends access by job ID without deleting SQLite values or descriptions.

Missing or expired jobs return HTTP `404`. This response does not distinguish a job that never existed from one whose retention period ended. If required stored data is missing, corrupt, or inconsistent, Lyra returns HTTP `500` with code `invalid_job_result`.

Repeated downloads cannot recover missing retained data. A new submission creates another job, which may reuse cached calculations.

Model Context Protocol (MCP) previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.
