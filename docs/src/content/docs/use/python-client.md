---
title: Use the Python client
description: Discover metrics and submit background jobs from synchronous or asynchronous Python code.
contentType: How-to
contentPlan: use/python-client
sidebar: { label: "Use The Python Client" }
---

Use the Python client when your calculation should run on a Lyra server. The client discovers the server’s metrics, submits jobs, and retrieves their results.

You need the `lyra-api` package, a server address, and an agent key. Follow [Install Lyra for Python](../../start/installation/) to prepare this repository’s environment or add the client to another project.

## Connect and inspect a metric

Set `LYRA_AGENT_API_KEY` to the key from your operator. Create `run_remote.py` and replace `lyra.example.com` with your server’s hostname. The script reads the catalog and inspects the example metric:

```python title="run_remote.py" {14}
import os

from lyra.api import LyraClient
from lyra.sdk.types import JsonObject

client = LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
catalog = client.catalog.metrics()
for metric in catalog:
    print(metric.name, metric.description)

metric = client.catalog.metric("city_street_maintenance")
print(metric.parameters)
print(metric.accepted_selections)
print(metric.outputs)
```

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

Your server must offer the example metric. Read its schema before choosing inputs, and check `metric.accepted_selections` for available collections and unit types.

## Search metrics and select an edition

Use `client.catalog.metrics(query="maintenance", limit=20)` to search metric families. Each family reports `requires_edition` and its public `editions`.

For a named edition, inspect `client.catalog.metric(name, edition="2025")`. Pass the same `edition="2025"` when submitting with any of these methods:

- `client.raw.submit()`
- `client.raw.run()`
- `client.raw.run_to_file()`

Search indexes accepted source types and preserves availability per edition; do not assume every edition accepts the same sources. Select an edition even when the family has only one named edition. For a family with an implicit edition, omit `edition`. See [spatial inputs](../spatial-inputs/) for named ID selections and how the server converts them to the metric’s inputs.

## Submit a job and wait

Append this request to `run_remote.py`. It selects a street from `example:city` and sets a condition threshold. For another metric, choose IDs from its inspected accepted selections:

```python title="run_remote.py" {9,12,14}
arguments: JsonObject = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
        "ids": ["street-1"],
    },
    "parameters": {"condition_threshold": 50},
}
handle = client.raw.submit("city_street_maintenance", arguments)
print(handle.job_id)

result = handle.wait(timeout=300)
print(result.outputs)
client.results.download(handle.job_id, "result.json")
```

Run `uv run python run_remote.py`. The `maintenance` output contains `street-1`, with `length_m` and `maintenance_length_m` both equal to 100. The file `result.json` contains the complete result envelope.

`submit()` waits for the server to validate and accept the request, then returns a job handle. Workers resolve locations during execution. Save `handle.job_id` so you can inspect the job later.

`wait()` checks status until the job succeeds, fails, or reaches your wait deadline. When the job succeeds, `wait()` returns a result containing its named outputs.

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
| Inspect output metadata and lifetime | `client.results.descriptor(job_id)` |

Import `RunOptions` from `lyra.api`. The `raw` methods accept JSON objects; the server validates them against the selected metric’s contract.

To reconstruct DataFrames and GeoDataFrames from the result JSON, use `client.result_tables(job_id)`. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Preview and retrieve results

Use `client.results.preview(handle.job_id)` to inspect at most five rows across all outputs. Call `client.results.descriptor(handle.job_id)` for output counts and metadata.

Use `client.results.get(handle.job_id)` or `client.results.download(handle.job_id, "result.json")` for authenticated full results. Results preserve available provenance, including the selected edition and cache revision.

## Use the asynchronous client

Create `run_remote_async.py` to submit the same request asynchronously. Start with its imports and request dictionary:

```python title="run_remote_async.py"
import asyncio
import os

from lyra.api import AsyncLyraClient
from lyra.sdk.types import JsonObject

arguments: JsonObject = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
        "ids": ["street-1"],
    },
    "parameters": {"condition_threshold": 50},
}
```

Append an async function that submits, waits, and reconstructs the result tables. Use the same hostname and `secure` setting as in the synchronous example:

```python title="run_remote_async.py" {6,7,8}
async def main() -> None:
    client = AsyncLyraClient(
        "lyra.example.com",
        agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
    )
    handle = await client.raw.submit("city_street_maintenance", arguments)
    result = await handle.wait(timeout=300)
    tables = await client.result_tables(handle.job_id)
    print(result.outputs)
    print(tables["maintenance"])


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python run_remote_async.py`. It returns the same street row as the synchronous example. In a notebook with an active event loop, use `await main()` instead of `asyncio.run(main())`.

With `AsyncLyraClient`, await catalog queries and job operations, including previews and full-result downloads. Await the asynchronous handle’s `status()`, `result()`, and `wait()` methods to perform the same operations.

## Handle errors

A successful `handle.status()` call can report a failed job. For that job, `handle.result()` and `handle.wait()` raise `MetricRunError`.

Catch `MetricRunError` when execution fails and `JobWaitTimeoutError` when your wait deadline expires. A wait timeout leaves the server job running. Save its ID and resume observation instead of submitting it again.

[Monitor jobs and handle failures](../jobs/) explains polling, progress callbacks, and retry behavior. Operators should use the separate [administrator client](../../operate/admin-cli/#use-an-administrator-client). Exact signatures and exception attributes are in the [Python reference](../../api/lyra/).

## Run the same calculation locally

The [city tutorial](../../plugins/tutorial/) runs the same calculation through `LocalRunner` using bundled data. Use it to explore selection, dependencies, aggregation, and provenance before connecting to a server.
