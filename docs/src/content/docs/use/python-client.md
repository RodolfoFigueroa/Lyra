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

```python title="run_remote.py" {14}
import os

from lyra.api import LyraClient
from lyra.contracts.types import JsonObject

with LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
) as client:
    catalog = client.catalog.metrics()
    for metric in catalog:
        print(metric.name, metric.description)

    metric = client.catalog.metric("city_street_maintenance")
    print(metric.parameters)
    print(metric.accepted_selections)
    print(metric.outputs)
```

Use a `with` block to close the client’s Hypertext Transfer Protocol (HTTP) session after success or failure. Without a `with` block, call `client.close()` in a `finally` block. Keep the client open while you use its job handles. Save job IDs to inspect those jobs through another client later.

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

Your server must offer the example metric. Read its schema before choosing inputs, and check `metric.accepted_selections` for available collections and unit types.

## Search metrics and select an edition

Use `client.catalog.metrics(query="maintenance", limit=20)` to search metric families. Each family reports `requires_edition` and its public `editions`.

For a named edition, inspect `client.catalog.metric(name, edition="2025")`. Pass the same `edition="2025"` when submitting with any of these methods:

- `client.raw.submit()`
- `client.raw.run()`
- `client.raw.run_to_file()`

Search results include the source types each edition accepts. Select an edition even when the family has only one named edition. For a family with an implicit edition, omit `edition`. See [spatial inputs](../spatial-inputs/) for named ID selections and how the server converts them to the metric’s inputs.

## Submit a job and wait

Append this request to `run_remote.py`. It selects a street from `example:city` and sets a condition threshold. For another metric, use a collection and unit type from `metric.accepted_selections`. Supply IDs that exist in that dataset.

```python title="run_remote.py" {13,16-17}
arguments: JsonObject = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
        "ids": ["street-1"],
    },
    "parameters": {"condition_threshold": 50},
}
with LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
) as client:
    handle = client.raw.submit("city_street_maintenance", arguments)
    print(handle.job_id)

    handle.wait(timeout=300)
    client.results.download(handle.job_id, "result.json")
```

Run `uv run python run_remote.py` and open `result.json`. In `outputs.maintenance`, the row for `street-1` has `length_m` and `maintenance_length_m` both equal to 100. The JavaScript Object Notation (JSON) file also contains the output declarations and execution provenance.

`submit()` waits for the server to validate and accept the request, then returns a job handle. Workers resolve locations during execution. Save `handle.job_id` so you can inspect the job later.

`wait()` checks status until the job succeeds, fails, or reaches your wait deadline. On success, `wait()` returns `JobStatusInfo`. To read the result in memory, call `handle.result()`. To save it to disk, use `client.results.download()` as in the example.

The server captures effective parameters with declared defaults before enqueueing. Omit `parameters` only when an empty object satisfies the selected family’s parameter schema. See [spatial inputs](../spatial-inputs/) for named ID selections and validation rules.

## Choose whether to submit, wait, or save

Pick the method that matches how your application manages background work:

| Task | Method |
| --- | --- |
| Submit now and check later | `client.raw.submit(name, arguments)` |
| Submit and wait in one call | `client.raw.run(name, arguments, options=RunOptions(timeout=300))` |
| Submit, wait, and save JSON | `client.raw.run_to_file(name, arguments, path="result.json", options=RunOptions(timeout=300))` |
| Inspect a saved job ID | `client.jobs.get(job_id)` |
| Fetch a terminal result | `client.results.get(job_id)` |
| Inspect output metadata and lifetime | `client.results.inspect(job_id)` |

Import `RunOptions` from `lyra.api`. The `raw` methods accept JSON objects; the server validates them against the selected metric’s contract.

To reconstruct DataFrames and GeoDataFrames from the result JSON, use `client.result_tables(job_id)`. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Preview and retrieve results

Call `client.results.inspect(handle.job_id)` to inspect a job before downloading its complete result. For a successful job, the response includes:

- Output metadata and numeric summaries
- At most five preview rows across all outputs
- Truncation fields describing omitted data

Use `client.results.get(handle.job_id)` to fetch the complete result, or `client.results.download(handle.job_id, "result.json")` to save it. Both authenticate with your agent key. Results include [provenance](../../concepts/provenance/), such as the selected edition and cache revision.

## Use the asynchronous client

Create `run_remote_async.py` to submit the same request asynchronously. Start with its imports and request dictionary:

```python title="run_remote_async.py"
import asyncio
import os

from lyra.api import AsyncLyraClient
from lyra.contracts.types import JsonObject

arguments: JsonObject = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
        "ids": ["street-1"],
    },
    "parameters": {"condition_threshold": 50},
}
```

This example uses `lyra[client,tables]` to reconstruct the result tables. Append an async function that submits the job and waits for it to finish. Use the same hostname and `secure` setting as in the synchronous example:

```python title="run_remote_async.py" {6-9}
async def main() -> None:
    async with AsyncLyraClient(
        "lyra.example.com",
        agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
    ) as client:
        handle = await client.raw.submit("city_street_maintenance", arguments)
        await handle.wait(timeout=300)
        tables = await client.result_tables(handle.job_id)
        print(tables["maintenance"])


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python run_remote_async.py`. It returns the same street row as the synchronous example. In a notebook with an active event loop, use `await main()` instead of `asyncio.run(main())`.

Use `async with` to close the session when the block ends. Without an `async with` block, call `await client.aclose()` in a `finally` block.

Run an async client’s requests and cleanup on the event loop where you first use it. You can run concurrent requests on that loop.

With `AsyncLyraClient`, await catalog queries and job operations, including previews and full-result downloads. Await the asynchronous handle’s `status()`, `result()`, and `wait()` methods to perform the same operations.

## Manage timeouts and cleanup

Set `timeout` when you create a client to limit the total time for each HTTP request or download. The default is 30s for both client modes.

Use `wait(timeout=...)` to limit status polling. For `run()` and `run_to_file()`, set `RunOptions(timeout=...)` to bound both polling and the final result transfer. This deadline starts after submission.

`run_to_file()` downloads once after completion. The client’s request timeout also limits each request, including the download.

If waiting times out or you cancel it, the client cancels the local request. The server job keeps running, and you can use the client again. A wait timeout can’t interrupt your callbacks or synchronous computation.

Closing the client waits for pending cleanup before releasing its HTTP session. Cleanup can continue beyond the wait timeout. You can call `close()` or `aclose()` more than once, but you can’t reuse a closed client.

## Handle errors

A successful `handle.status()` call can report a failed job. For that job, `handle.result()` and `handle.wait()` raise `MetricRunError`.

Catch `MetricRunError` when execution fails and `JobWaitTimeoutError` when your wait deadline expires. A wait timeout leaves the server job running. Save its ID and resume observation instead of submitting it again.

[Monitor jobs and handle failures](../jobs/) explains polling, progress callbacks, and retry behavior. Operators should use the separate [administrator client](../../operate/admin-cli/#use-an-administrator-client). Exact signatures and exception attributes are in the [Python reference](../../api/lyra/).

## Run the same calculation locally

The [city tutorial](../../plugins/tutorial/) runs the same calculation through `LocalRunner` using bundled data. Use it to explore selection, dependencies, aggregation, and provenance before connecting to a server.
