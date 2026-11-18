---
title: Use the Python client
description: Discover metrics and submit background jobs from synchronous or asynchronous Python code.
contentType: How-to
contentPlan: use/python-client
sidebar: { label: "Use The Python Client" }
---

Run calculations on a Lyra server with the Python client. Discover available metrics and submit jobs, then inspect or download their results.

Get the server address and an agent key from your operator. Install `lyra[client]` to submit jobs and download results. Choose `lyra[client,data]` to read retained Arrow tables as pandas or GeoPandas frames, or `lyra[client,tables]` for job-based JSON table conversion. See the [installation commands](../../start/installation/#add-lyra-to-an-existing-project).

## Connect and inspect a metric

Set `LYRA_AGENT_API_KEY` to the key from your operator. Create `run_remote.py` and replace `lyra.example.com` with your server’s hostname. The script reads the catalog and inspects the example metric:

```python title="run_remote.py" {17}
import os

import httpx

from lyra.api import LyraClient
from lyra.contracts.types import JsonObject

with LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
    timeout=httpx.Timeout(30.0, connect=5.0, pool=5.0),
) as client:
    catalog = client.catalog.metrics()
    for metric in catalog:
        print(metric.name, metric.description)

    metric = client.catalog.metric("city_street_maintenance")
    print(metric.parameters)
    print(metric.accepted_selections)
    print(metric.outputs)
```

The example allows 5s to establish a connection or wait for an available connection from the pool. Each network read or write can wait up to 30s.

Use a `with` block to close the client’s HTTP session after success or failure. Without a `with` block, call `client.close()` in a `finally` block. Keep the client open while you use its job handles. Save job IDs to inspect those jobs through another client later.

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

Your server must offer the example metric. Read its schema before choosing inputs, and check `metric.accepted_selections` for available providers.

## Search and select metrics

Use `client.catalog.metrics(query="maintenance", limit=20)` to search registered public metrics. Each result lists accepted source types and describes what each output row represents.

Inspect `client.catalog.metric(name)` to read the [metric’s request and output contracts](../rest-api/#read-the-live-contract). Submit the registered name with any of these methods:

- `client.jobs.create()`
- `client.jobs.submit()`
- `client.jobs.run()`
- `client.jobs.run_to_file()`

Search results include the source types each metric accepts. See [spatial inputs](../spatial-inputs/) for named ID selections and how the server converts them to the metric’s inputs.

## Submit a job and wait

Append this request to `run_remote.py`. It selects a street from `city_streets` and sets a condition threshold. For another metric, choose a provider from `metric.accepted_selections`. Supply IDs that exist in that dataset.

```python title="run_remote.py" {11-15,18-19}
inputs: JsonObject = {
    "streets": {
        "provider": "city_streets",
        "ids": ["street-1"],
    },
}
with LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
) as client:
    handle = client.jobs.submit(
        "city_street_maintenance",
        inputs=inputs,
        parameters={"condition_threshold": 50},
    )
    print(handle.job_id)

    status = handle.wait(timeout=300)
    print(status.execution_id, status.result_ref)
    client.results.download(handle.job_id, "result.json")
```

Run `uv run python run_remote.py` and open `result.json`. In `outputs.maintenance`, the row for `street-1` has `length_m` and `maintenance_length_m` both equal to 100. The JSON file also contains the output declarations and execution provenance.

`submit()` waits for the server to validate and accept the request, then returns a job handle. Workers resolve locations during execution. Save `handle.job_id` so you can inspect the job later.

`wait()` checks status until the job succeeds, fails, or reaches your wait deadline. On success, it returns `JobStatusInfo` with `execution_id` and `result_ref`, without downloading tables. Save these identifiers with the deployment address. To read the result in memory, call `handle.result()`; it and `client.jobs.run()` return the same identifiers. To save the result to disk, use `client.results.download()` as in the example.

Omit `inputs` or `parameters`, or pass `None`, to use an empty mapping. You can also unpack a mapping with these keys using `**request`.

Before enqueueing, the server validates your parameters and fills in declared defaults. Omit `parameters` only when an empty object satisfies the selected metric’s parameter schema. See [spatial inputs](../spatial-inputs/) for named ID selections and validation rules.

To submit a [street proposal](../local-execution/#run-with-a-street-proposal) with either Python client, pass `interventions=[proposal]` to any job submission method. Use `lyra[client,data]` for GeoDataFrame additions. The client checks the frame against the provider’s declared column types and CRS, then sends the records inline.

For a run against the base datasets, omit `interventions`, pass `None`, or pass an empty list.

## Choose whether to submit, wait, or save

Pick the method that matches how your application manages background work:

| Task | Method |
| --- | --- |
| Submit and return the acceptance response | `client.jobs.create(name, inputs=inputs, parameters=parameters)` |
| Submit now and check later | `client.jobs.submit(name, inputs=inputs, parameters=parameters)` |
| Submit and wait in one call | `client.jobs.run(name, inputs=inputs, parameters=parameters, options=RunOptions(timeout=300))` |
| Submit, wait, and save JSON | `client.jobs.run_to_file(name, inputs=inputs, parameters=parameters, path="result.json", options=RunOptions(timeout=300))` |
| Inspect a saved job ID | `client.jobs.get(job_id)` |
| Fetch a terminal result | `client.results.get(job_id)` |
| Inspect output metadata and lifetime | `client.results.inspect(job_id)` |

Each submission helper creates a new, independent job. Status lookup and handle `wait()` only observe existing jobs; `wait()` does not download results.

Import `RunOptions` from `lyra.api`. The job submission methods accept JSON objects; the server validates them against the selected metric’s contract.

To reconstruct DataFrames and GeoDataFrames from the result JSON, use `client.result_tables(job_id)`. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Read retained metadata

Save the `result_ref` from a successful job response. Call `client.results.describe(result_ref)` to read its complete [result description](../results/#read-a-retained-description). This lookup reads metadata without loading tables or the full report.

Use `await client.results.describe(result_ref)` with `AsyncLyraClient`. Both clients return complete metadata, automatically downloading descriptions that exceed the inspection limit. You can reuse the reference after job expiry or plugin registration changes, while the retained store remains available.

### Reconnect with durable identifiers

Use the identifiers from successful job status to reconnect after the job expires. Install `lyra[client,data]` and set these environment variables before running the example:

| Variable | Value |
| --- | --- |
| `LYRA_SERVER` | Original hostname and public path prefix, such as `lyra.example.com/api` |
| `LYRA_AGENT_API_KEY` | Your agent credential |
| `LYRA_RESULT_REF` | Saved `result_ref` |
| `LYRA_EXECUTION_ID` | Saved `execution_id` |
| `LYRA_OUTPUT_NAME` | Declared output name, such as `maintenance` |

Save this script as `read_retained.py`. It retrieves complete metadata and a native table, then saves the table and report:

```python title="read_retained.py" {12-18}
import os

from lyra.api import LyraClient

result_ref = os.environ["LYRA_RESULT_REF"]
execution_id = os.environ["LYRA_EXECUTION_ID"]
output_name = os.environ["LYRA_OUTPUT_NAME"]
with LyraClient(
    os.environ["LYRA_SERVER"],
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
) as client:
    description = client.results.describe(result_ref)
    report = client.reports.get(execution_id)
    assert description.execution_id == report.execution_id
    assert report.result_ref == result_ref
    table = client.results.table(result_ref, output_name)
    client.results.download_table(result_ref, output_name, "output.arrow")
    client.reports.download(execution_id, "report.json")

print(description.outputs[output_name].owner_scope)
print(table)
```

Run `uv run python read_retained.py`. Both lookups return complete models, even when an inspection would return a preview. You can use the models and frame after the client closes.

For an async application, open `AsyncLyraClient` with `async with` and await the same methods. Job methods such as `results.get(job_id)` require an unexpired job record. Use the methods above with saved result and execution identifiers after expiry.

## Read and save retained tables

Use `result_ref` and an output name from `description.outputs` to read a native table:

```python
maintenance = client.results.table(result_ref, "maintenance")
client.results.download_table(result_ref, "maintenance", "maintenance.arrow")
```

With `AsyncLyraClient`, await each call. `table()` requires `lyra[client,data]` and returns an independent DataFrame or GeoDataFrame with declared nullable types and native geometry/CRS. Returned frames remain usable after client closure. Provider ownership stays in the separate result description.

`download_table()` requires only `lyra[client]`. It streams unchanged Arrow IPC bytes regardless of the filename extension, replaces the destination after successful completion, and returns `None`. Create the parent directory first. Failure or cancellation preserves any original file and cleans up the temporary file.

Both operations work after job expiry while the retained store is available. See the [results guide](../results/#read-a-retained-table) for ordinary PyArrow/GeoPandas file readers and lookup errors.

## Iterate retained rows

Call `client.results.rows(result_ref, output_name)` to read a page. Pass each returned `next_cursor` unchanged until it is `None`, even after a short page. With the async client, await each call.

Integer cells are decimal strings. Geometry cells use well-known text (WKT) in the saved coordinate reference system (CRS). Use [native Arrow tables](#read-and-save-retained-tables) to restore nullable types and geometry objects.

See [row iteration and oversized-row recovery](../results/#read-paginated-rows) for an executable loop. If a row exceeds the page limit, `rows()` raises `RowTooLargeError` without skipping it. Use the exception’s authenticated Arrow download URL to retrieve the complete output.

## Read retained location inputs

Read a result’s [location inputs](../spatial-inputs/) to inspect the records used in its calculation. Each available input contains the complete selection, including reused records. Choose its name from `description.location_inputs`; inspecting the description doesn’t load input tables.

For the street maintenance example, install `lyra[client,data]` to read `streets` as a GeoDataFrame. This code reads the input and saves an Arrow IPC copy:

```python {3-4}
with LyraClient("lyra.example.com", agent_api_key="your_agent_key_here") as client:
    description = client.results.describe(result_ref)
    streets = client.results.location_input(result_ref, "streets")
    client.results.download_location_input(result_ref, "streets", "streets.arrow")

print(streets.geometry)
```

You can edit the returned GeoDataFrame without changing retained records or later reads, even after closing the client. Its active geometry retains the declared CRS, and nullable columns preserve their types. Known empty inputs return empty frames with their declared schema.

Direct inputs contain unique provider records. Operation-derived inputs contain source-to-target correspondences: `source_location_id`/`location_id` pairs with the target’s attributes and geometry. A target appears once for each source that selects it. Read the source and target provider scopes from `description.location_inputs[input_name]`.

### Inspect input rows

Use `location_input_rows()` to inspect a page with the same [JSON cell encodings as output pages](#iterate-retained-rows). Pass each `next_cursor` unchanged to the same result and input until it is `None`:

```python {2,7-9}
with LyraClient("lyra.example.com", agent_api_key="your_agent_key_here") as client:
    page = client.results.location_input_rows(result_ref, "streets")
    while True:
        print(page.rows)
        if page.next_cursor is None:
            break
        page = client.results.location_input_rows(
            result_ref, "streets", cursor=page.next_cursor
        )
```

Known empty inputs return an empty page. If a row cannot fit in JSON, use `RowTooLargeError.download_url` to download the complete input as Arrow IPC.

### Download an input

Use `download_location_input(result_ref, input_name, destination)` to save an input without loading a GeoDataFrame. Install `lyra[client]` and pass a string or `Path` destination. The method returns `None`.

Create the destination’s parent directory before downloading. The client replaces an existing file only after the download completes successfully. Failure or cancellation preserves your original file and removes the temporary file; filesystem errors propagate to your code.

Use the same arguments with `await` on `AsyncLyraClient`. Both clients return complete descriptions, automatically downloading metadata when it exceeds the inspection limit.

### Handle unavailable inputs

Check an input’s `availability` in its description before reading it. The value `not_materialized` means execution skipped the input without retaining its complete selection. Reading that input raises `LocationInputUnavailableError` with `result_ref` and `input_name`; it doesn’t acquire data. Other lookup and pagination errors identify the input through `input_name` and set `output_name=None`.

## Retrieve execution reports

Save `execution_id` from a successful job response to [retrieve its report](../results/#read-an-execution-report), including after job expiry. Choose the method for the response you need:

| Task | Method |
| --- | --- |
| Get the complete validated report | `client.reports.get(execution_id)` |
| Inspect a complete report or bounded preview | `client.reports.inspect(execution_id)` |
| Stream report JSON to a file | `client.reports.download(execution_id, "report.json")` |

Use `await` with each method on `AsyncLyraClient`. Report retrieval doesn’t load output tables and requires the retained store to remain available.

Call `get()` whenever you need the complete report, including when `inspect()` returns a preview. You can use the returned model after closing the client; editing it doesn’t affect later reads.

Create the destination’s parent directory before calling `download()`. The method returns `None` and replaces an existing file only after the download finishes and its resources close successfully. If the download fails or you cancel it, the client removes the temporary file and preserves your original destination.

Read [report inspection responses](../results/#choose-an-inspection-or-complete-download) to handle previews. For failures, use the [report lookup errors](../results/#handle-report-lookup-failures).

## Preview and retrieve results

Call `client.results.inspect(handle.job_id)` to inspect a job before downloading its complete result. For a successful job, the response includes:

- Output metadata and numeric summaries
- At most five preview rows across all outputs
- Truncation fields describing omitted data

Call `client.results.get(handle.job_id)` to fetch the complete result. To save it, call `client.results.download(handle.job_id, "result.json")`. Both methods authenticate with your agent key and accept job IDs.

Read the result’s `execution_id` to identify its saved report. Call `client.reports.get(execution_id)` to retrieve it, or [reopen the report locally](../results/#reopen-a-local-result) with `ResultStore`. The result’s [provenance](../../concepts/provenance/) field records the accepted plan and available observations.

Check the inspection’s `status` to determine whether the job is active, successful, or failed. Inspecting a failed job returns its state without raising an execution error.

Result retrieval depends on the job’s state:

- While queued or running, `client.results.get()` and `client.results.download()` raise `JobNotReadyError`.
- For failed jobs, `client.results.get()` returns the failure result and `client.results.download()` saves it.
- For failed jobs, `handle.result()` and table helpers raise `MetricRunError`.

Use the exact nonempty `job_id` string for job-based lookups. The client preserves whitespace and rejects empty strings and non-string IDs before requests or file writes. For metadata lookup, pass the separate `result_ref` to `results.describe()`.

Missing or expired jobs return HTTP `404`. If required stored data is missing, corrupt, or inconsistent, Lyra returns HTTP `500` with code `invalid_job_result`. The client raises `DownloadError` for either response. Repeating a lookup does not rerun the calculation or recover missing data.

## Use the asynchronous client

Create `run_remote_async.py` to submit the same request asynchronously. Start with its imports and request dictionary:

```python title="run_remote_async.py"
import asyncio
import os

from lyra.api import AsyncLyraClient
from lyra.contracts.types import JsonObject

inputs: JsonObject = {
    "streets": {
        "provider": "city_streets",
        "ids": ["street-1"],
    },
}
```

This example uses `lyra[client,tables]` to reconstruct the result tables. Append an async function that submits the job and waits for it to finish. Use the same hostname and `secure` setting as in the synchronous example:

```python title="run_remote_async.py" {6-12}
async def main() -> None:
    async with AsyncLyraClient(
        "lyra.example.com",
        agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
    ) as client:
        handle = await client.jobs.submit(
            "city_street_maintenance",
            inputs=inputs,
            parameters={"condition_threshold": 50},
        )
        await handle.wait(timeout=300)
        tables = await client.result_tables(handle.job_id)
        print(tables["maintenance"])


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python run_remote_async.py`. It returns the same street row as the synchronous example. In a notebook with an active event loop, use `await main()` instead of `asyncio.run(main())`.

Use `async with` to close the session when the block ends. Without an `async with` block, call `await client.aclose()` in a `finally` block.

Keep the client within one async application lifecycle. Concurrent requests share its connection pool.

With `AsyncLyraClient`, await catalog queries and job operations, including previews and full-result downloads. Await the asynchronous handle’s `status()`, `result()`, and `wait()` methods to perform the same operations.

## Manage timeouts and cleanup

Set `timeout` when you create a consumer or administrator client in either mode. Choose how HTTPX, the client’s HTTP library, limits waiting:

- Pass a number of seconds to set all HTTP timeouts; the default is `30.0`
- Pass an `httpx.Timeout` to configure connection, read, write, and pool timeouts separately
- Pass `None` to disable HTTP timeouts

A read timeout limits how long each network read can wait. A download can exceed this timeout if data keeps arriving.

Use `wait(timeout=...)` to limit status polling. For `run()` and `run_to_file()`, set `RunOptions(timeout=...)` for polling and the final result transfer. This deadline starts after submission and is checked before and after operations. Requests and cleanup already in progress may finish after the deadline; their HTTP timeouts remain independent.

`run_to_file()` downloads once after completion. HTTP timeout settings apply to each request and download.

A wait deadline does not interrupt an in-flight request, callback, or cleanup. Local timeout or cancellation leaves the server job running. A later standalone `handle.result()` uses the client’s HTTP timeout.

Synchronous operations run in your calling thread, including progress callbacks and file writes. Finish operations before calling `close()` to release the client’s connections.

Asynchronous operations run in the caller’s task. Finish outstanding operations, or cancel and await them, before calling `aclose()` to release connections. Cancellation propagates through cleanup, which finishes before the operation settles. You can call `close()` or `aclose()` more than once, but you can’t reuse a closed client.

When an application stops concurrent work, settle its task inside the client context:

```python
import asyncio
from contextlib import suppress

from lyra.api import AsyncLyraClient


async def run_until_stopped(stop: asyncio.Event) -> None:
    async with AsyncLyraClient(
        "lyra.example.com",
        agent_api_key="your_agent_key_here",
    ) as client:
        task = asyncio.create_task(client.jobs.run("example_metric"))
        try:
            await stop.wait()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
```

## Handle errors

A successful `handle.status()` call can report a failed job. For that job, `handle.result()` and `handle.wait()` raise `MetricRunError`.

Catch `MetricRunError` when execution fails and `JobWaitTimeoutError` when your wait deadline expires. A wait timeout leaves the server job running. Save its ID and resume observation instead of submitting it again.

[Monitor jobs and handle failures](../jobs/) explains polling, progress callbacks, and retry behavior. Operators should use the separate [administrator client](../../operate/admin-cli/#use-an-administrator-client). Exact signatures and exception attributes are in the [Python reference](../../api/lyra/).

## Run the same calculation locally

The [city tutorial](../../plugins/tutorial/) runs the same calculation through `LocalRunner` using bundled data. Use it to explore selection, dependencies, aggregation, and provenance before connecting to a server.

## Export spatial tables to GeoParquet

Install `lyra[client,data]` to export retained spatial tables to your local filesystem. Choose the method for your selection:

- `client.results.export_geoparquet(result_ref, output_name, path)` for a named spatial output
- `client.results.export_location_input_geoparquet(result_ref, input_name, path)` for a retained location input

Both methods accept `str` or `Path` destinations and return `None`. Await them with `AsyncLyraClient`. They read retained originals and complete descriptions independently of your edits to previously returned values.

See [export spatial tables to GIS files](../location-exports/) for examples and metadata details. The guide also explains destination replacement and cleanup after failure or cancellation.
