---
title: Use the Python client
description: Discover metrics and submit background jobs from synchronous or asynchronous Python code.
contentType: How-to
contentPlan: use/python-client
sidebar: { label: "Use The Python Client" }
---

Run calculations on a Lyra server with the Python client. Discover available metrics and submit jobs, then inspect or download their results.

Get the server address and an agent key from your operator. Install `lyra[client]` to submit jobs and download results. Choose `lyra[client,tables]` if you also want pandas or GeoPandas tables. See the [installation commands](../../start/installation/#add-lyra-to-an-existing-project).

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

Use a `with` block to close the client’s Hypertext Transfer Protocol (HTTP) session after success or failure. Without a `with` block, call `client.close()` in a `finally` block. Keep the client open while you use its job handles. Save job IDs to inspect those jobs through another client later.

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

Your server must offer the example metric. Read its schema before choosing inputs, and check `metric.accepted_selections` for available collections and unit types.

## Search and select metrics

Use `client.catalog.metrics(query="maintenance", limit=20)` to search registered public metrics. Each result lists accepted source types and describes what each output row represents.

Inspect `client.catalog.metric(name)` to read the [metric’s request and output contracts](../rest-api/#read-the-live-contract). Submit the registered name with any of these methods:

- `client.jobs.create()`
- `client.jobs.submit()`
- `client.jobs.run()`
- `client.jobs.run_to_file()`

Search results include the source types each metric accepts. See [spatial inputs](../spatial-inputs/) for named ID selections and how the server converts them to the metric’s inputs.

## Submit a job and wait

Append this request to `run_remote.py`. It selects a street from `example:city` and sets a condition threshold. For another metric, use a collection and unit type from `metric.accepted_selections`. Supply IDs that exist in that dataset.

```python title="run_remote.py" {12-16,19-20}
inputs: JsonObject = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
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

Run `uv run python run_remote.py` and open `result.json`. In `outputs.maintenance`, the row for `street-1` has `length_m` and `maintenance_length_m` both equal to 100. The JavaScript Object Notation (JSON) file also contains the output declarations and execution provenance.

`submit()` waits for the server to validate and accept the request, then returns a job handle. Workers resolve locations during execution. Save `handle.job_id` so you can inspect the job later.

`wait()` checks status until the job succeeds, fails, or reaches your wait deadline. On success, `wait()` returns `JobStatusInfo`. To read the result in memory, call `handle.result()`. To save it to disk, use `client.results.download()` as in the example.

Omit `inputs` or `parameters`, or pass `None`, to use an empty mapping. You can also unpack a mapping with these keys using `**request`.

Before enqueueing, the server validates your parameters and fills in declared defaults. Omit `parameters` only when an empty object satisfies the selected metric’s parameter schema. See [spatial inputs](../spatial-inputs/) for named ID selections and validation rules.

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

Pass optional `cache_revision` to submission helpers to [control cache reuse](../../concepts/caching/) for the whole run.

Each submission helper creates a new, independent job. Status lookup and handle `wait()` only observe existing jobs; `wait()` does not download results.

Import `RunOptions` from `lyra.api`. The job submission methods accept JSON objects; the server validates them against the selected metric’s contract.

To reconstruct DataFrames and GeoDataFrames from the result JSON, use `client.result_tables(job_id)`. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Preview and retrieve results

Call `client.results.inspect(handle.job_id)` to inspect a job before downloading its complete result. For a successful job, the response includes:

- Output metadata and numeric summaries
- At most five preview rows across all outputs
- Truncation fields describing omitted data

Use `client.results.get(handle.job_id)` to fetch the complete result, or `client.results.download(handle.job_id, "result.json")` to save it. Both authenticate with your agent key. Results include [provenance](../../concepts/provenance/), such as the selected metric and cache revision.

Check the inspection’s `status` to determine whether the job is active, successful, or failed. Inspecting a failed job returns its state without raising an execution error.

Result retrieval depends on the job’s state:

- While queued or running, `client.results.get()` and `client.results.download()` raise `JobNotReadyError`.
- For failed jobs, `client.results.get()` returns the failure result and `client.results.download()` saves it.
- For failed jobs, `handle.result()` and table helpers raise `MetricRunError`.

Use the exact nonempty `job_id` string for every lookup. The client preserves whitespace and rejects empty strings and other types before making requests or opening files.

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
        "collection": "example:city",
        "unit_type": "street",
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
    async with AsyncLyraClient("lyra.example.com", agent_api_key="agent-key") as client:
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
