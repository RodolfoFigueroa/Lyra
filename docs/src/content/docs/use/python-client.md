---
title: Use the Python client
description: Discover metrics and submit background jobs from synchronous or asynchronous Python code.
contentType: How-to
contentPlan: use/python-client
sidebar: { label: "Use The Python Client" }
---

Run calculations on a Lyra server with the Python client. Discover available metrics and submit jobs, then inspect or download their results.

Get the server address and an agent key from your operator. Install `lyra[client]` to submit jobs and download results. Install `lyra[client,tables]` to read retained Arrow tables as pandas or GeoPandas frames. See the [installation commands](../../start/installation/#add-lyra-to-an-existing-project).

## Connect and inspect a metric

Set `LYRA_AGENT_API_KEY` to the key from your operator. Create `run_remote.py` and replace `lyra.example.com` with your server’s hostname. The script reads the catalog and inspects the example metric:

```python title="run_remote.py" {17,19}
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
    print(metric.metric.accepted_selections["streets"])
    print(metric.outputs)
```

The example allows 5s to establish a connection or wait for an available connection from the pool. Each network read or write can wait up to 30s.

Use a `with` block to close the client’s HTTP session after success or failure. Without a `with` block, call `client.close()` in a `finally` block. Keep the client open while you use its job handles. Save job IDs to inspect those jobs through another client later.

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

The example requires `city_street_maintenance` on your server. The script prints the provider IDs you can use for its `streets` input.

## Search and select metrics

Use `client.catalog.metrics(query="maintenance", limit=20)` to find public metrics. Each summary lists accepted source provider IDs and describes what each output row represents.

Inspect `client.catalog.metric(name)` to read the [metric’s request and output contracts](../rest-api/#read-the-live-contract). Submit the registered name with any of these methods:

- `client.jobs.create()`
- `client.jobs.submit()`
- `client.jobs.run()`
- `client.jobs.run_to_file()`

For a different metric, use each input’s name to look up its provider IDs in the summary’s `accepted_selections`. See [spatial inputs](../spatial-inputs/) for the selection format.

## Submit a job and wait

Append this request to `run_remote.py`. It selects `street-1` from `city_streets` and sets the maintenance threshold to 50.

```python title="run_remote.py" {11-16,18-19}
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

    handle.wait(timeout=300)
    client.results.download(handle.job_id, "result.json")
```

Run `uv run python run_remote.py` and open `result.json`. In `outputs.maintenance`, the row for `street-1` has `length_m` and `maintenance_length_m` both equal to 100. The JSON file also contains the output declarations and execution provenance.

`submit()` waits for the server to validate and accept the request, then returns a job handle. Workers resolve locations during execution. Save `handle.job_id` so you can inspect the job later.

Call `handle.wait()` to poll until the job succeeds, fails, or reaches your deadline. On success it returns a `JobStatusInfo` with the same job ID. To read the result in memory, call `handle.result()`. To write the JSON to disk, call `client.results.download()`.

Omit `inputs` or `parameters`, or pass `None`, to use an empty mapping. You can also unpack a mapping with these keys using `**request`.

Before enqueueing, the server validates your parameters and fills in declared defaults. Omit `parameters` only when an empty object satisfies the selected metric’s parameter schema. See [spatial inputs](../spatial-inputs/) for named ID selections and validation rules.

To submit a [street proposal](../local-execution/#run-with-a-street-proposal) with either Python client, pass `interventions=[proposal]` to any job submission method. Use `lyra[client,data]` for GeoDataFrame additions. For populated additions, the client fetches metric discovery, checks the provider’s declared column types and CRS, and sends the records inline. A request containing only removals goes directly to job submission.

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

To retrieve every output as a DataFrame or GeoDataFrame, use `client.result_tables(job_id)` while the successful job is still visible. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Read retained metadata

Call `client.results.describe(job_id)` to read the [result description](../results/#read-a-retained-description) without loading tables or the execution report. Use the ID you saved at submission.

With `AsyncLyraClient`, await the same method. Both clients fetch the complete description even when it exceeds the inspection limit.

### Reconnect with the job ID

To read a saved run after queue expiry, connect to the original deployment with the same job ID. Install `lyra[client,tables]` and set the following environment variables.

| Variable | Value |
| --- | --- |
| `LYRA_SERVER` | Original hostname and public path prefix, such as `lyra.example.com/api` |
| `LYRA_AGENT_API_KEY` | Your agent credential |
| `LYRA_JOB_ID` | Saved `job_id` |
| `LYRA_OUTPUT_NAME` | Declared output name, such as `maintenance` |

Save this script as `read_retained.py`. It retrieves complete metadata and a native table, then saves the table and report:

```python title="read_retained.py" {11-16}
import os

from lyra.api import LyraClient

job_id = os.environ["LYRA_JOB_ID"]
output_name = os.environ["LYRA_OUTPUT_NAME"]
with LyraClient(
    os.environ["LYRA_SERVER"],
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
) as client:
    description = client.results.describe(job_id)
    report = client.reports.get(job_id)
    assert description.job_id == report.job_id == job_id
    table = client.results.table(job_id, output_name)
    client.results.download_table(job_id, output_name, "output.arrow")
    client.reports.download(job_id, "report.json")

print(description.outputs[output_name].owner_scope)
print(table)
```

Run `uv run python read_retained.py`. Both lookups return complete models, even when an inspection would return a preview. You can use the models and frame after the client closes.

With `AsyncLyraClient`, use `async with` and await each method. Direct report and table reads work after queue expiry. The job-result method `client.results.get(job_id)` reads completed outcomes from SQLite.

## Read and save retained tables

Use `job_id` and an output name from `description.outputs` to read a native table:

```python
maintenance = client.results.table(job_id, "maintenance")
client.results.download_table(job_id, "maintenance", "maintenance.arrow")
```

With `AsyncLyraClient`, await each call. `table()` requires `lyra[client,tables]` and returns an independent DataFrame or GeoDataFrame with declared nullable types and native geometry/CRS. Returned frames remain usable after client closure. Provider ownership stays in the separate result description.

`download_table()` requires only `lyra[client]`. It streams unchanged Arrow IPC bytes regardless of the filename extension, replaces the destination after successful completion, and returns `None`. Create the parent directory first. Failure or cancellation preserves any original file and cleans up the temporary file.

Both operations work after queue expiry while the retained store is available. See the [results guide](../results/#read-a-retained-table) for ordinary PyArrow/GeoPandas file readers and lookup errors.

Use `client.results.inspect(job_id)` for a bounded sample of metric outputs. For a subset of a complete table, filter the downloaded frame locally, for example `maintenance.loc[maintenance["length_m"] > 50]`.

## Read retained location inputs

Read a result’s [location inputs](../spatial-inputs/) to inspect the records used in its calculation. Each available input contains the complete selection, including reused records. Choose its name from `description.location_inputs`; inspecting the description doesn’t load input tables.

For the street maintenance example, install `lyra[client,tables]` to read `streets` as a GeoDataFrame. Inside your client context, read the input and save a copy as an Arrow IPC stream.

```python {1-2}
streets = client.results.location_input(job_id, "streets")
client.results.download_location_input(job_id, "streets", "streets.arrow")

print(streets.geometry)
```

You can edit the returned GeoDataFrame without changing retained records or later reads, even after closing the client. Its active geometry retains the declared CRS, and nullable columns preserve their types. Known empty inputs return empty frames with their declared schema.

Direct inputs contain unique provider records. Operation-derived inputs contain source-to-target correspondences: `source_location_id`/`location_id` pairs with the target’s attributes and geometry. A target appears once for each source that selects it. Read the source and target provider scopes from `description.location_inputs[input_name]`.

### Download an input

Use `download_location_input(job_id, input_name, destination)` to save an input without loading a GeoDataFrame. Install `lyra[client]` and pass a string or `Path` destination. The method returns `None`.

Create the destination’s parent directory before downloading. The client replaces an existing file only after the download completes successfully. Failure or cancellation preserves your original file and removes the temporary file; filesystem errors propagate to your code.

Use the same arguments with `await` on `AsyncLyraClient`. Both clients return complete descriptions, fetching metadata in one GET at every size.

### Handle unavailable inputs

Check `availability` before reading an input. A value of `not_materialized` means the run skipped the input and didn’t save its complete selection. Reading it raises `LocationInputUnavailableError` with the job ID and input name. Other input lookup errors also include `output_name=None`.

## Retrieve execution reports

Save `job_id` from submission to [retrieve its report](../results/#read-an-execution-report), including after queue expiry. Choose the method for the response you need:

| Task | Method |
| --- | --- |
| Get the complete validated report | `client.reports.get(job_id)` |
| Inspect a complete report or bounded preview | `client.reports.inspect(job_id)` |
| Stream report JSON to a file | `client.reports.download(job_id, "report.json")` |

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

To read the saved report, call `client.reports.get(job_id)` or [open the store locally](../results/#reopen-a-local-result). To compare the accepted request with observations from this run, inspect the result’s [provenance](../../concepts/provenance/).

Check the inspection’s `status` to determine whether the job is active, successful, or failed. Inspecting a failed job returns its state without raising an execution error.

Result retrieval depends on the job’s state:

- While queued or running, `client.results.get()` and `client.results.download()` raise `JobNotReadyError`.
- For failed jobs, `client.results.get()` returns the failure result and `client.results.download()` saves it.
- For failed jobs, `handle.result()` and table helpers raise `MetricRunError`.

Both clients validate that the job ID contains exactly 32 lowercase hexadecimal characters before sending a request.

Unknown jobs return HTTP `404`. If required stored data is missing, corrupt, or inconsistent, Lyra returns HTTP `500` with code `invalid_job_result`. The client raises `DownloadError` for either response. Repeating a lookup does not rerun the calculation or recover missing data.

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

- `client.results.export_geoparquet(job_id, output_name, path)` for a named spatial output
- `client.results.export_location_input_geoparquet(job_id, input_name, path)` for a retained location input

Both methods accept `str` or `Path` destinations and return `None`. Await them with `AsyncLyraClient`. They read retained originals and complete descriptions independently of your edits to previously returned values.

See [export spatial tables to GIS files](../location-exports/) for examples and metadata details. The guide also explains destination replacement and cleanup after failure or cancellation.
