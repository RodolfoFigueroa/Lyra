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

Create a client with your server’s hostname and agent key, then read the catalog and inspect the example metric:

```python
import os

from lyra.api import LyraClient

client = LyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
catalog = client.catalog.metrics()
for metric in catalog.metrics:
    print(metric.name, metric.description)

metric = client.catalog.metric("smoke_table_metric")
print(metric.request_schema)
print(metric.outputs)
```

Pass a hostname without a URL scheme. HTTPS is the default. For local HTTP, use `LyraClient("localhost:5219", secure=False, agent_api_key=...)`.

The example metric must be enabled on your server. Read its live schema before choosing inputs: each deployment can offer different plugins.

## Submit a job and wait

The example metric accepts a region and an integer to copy into its output rows:

```python
from lyra.sdk.types import JsonObject

arguments: JsonObject = {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"value": 7},
}
handle = client.raw.submit("smoke_table_metric", arguments)
print(handle.job_id)

result = handle.wait(timeout=300)
print(result.outputs)
client.results.download(handle.job_id, "result.json")
```

`submit()` returns a job handle immediately. `wait()` checks status until the job succeeds, fails, or reaches your wait deadline. A successful call returns the named-output result. Save `handle.job_id` so you can inspect the job later.

A metric with a parameter model requires a `parameters` object, even if all fields have defaults. A metric without that model omits the property. See [spatial inputs](../spatial-inputs/) for other ways to specify a region.

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

Import `RunOptions` from `lyra.api`. The `raw` methods accept JSON objects; the server validates them against the selected metric’s request schema.

For native DataFrames and GeoDataFrames, use `client.result_tables(job_id)`. The [results guide](../results/) explains output names, geometry, and what survives reconstruction.

## Use the asynchronous client

In an async function or a notebook with top-level await:

```python
import os

from lyra.api import AsyncLyraClient

client = AsyncLyraClient(
    "lyra.example.com",
    agent_api_key=os.environ["LYRA_AGENT_API_KEY"],
)
handle = await client.raw.submit(
    "smoke_table_metric",
    {
        "location": {"data_type": "met_zone_code", "value": "09.01"},
        "parameters": {"value": 7},
    },
)
result = await handle.wait(timeout=300)
tables = await client.result_tables(handle.job_id)
```

Await catalog, submission, status, result, and download operations. The asynchronous job handle provides the same `status()`, `result()`, and `wait()` operations as the synchronous handle.

## Handle errors

Catch `MetricRunError` when execution fails and `JobWaitTimeoutError` when your wait deadline expires. A wait timeout leaves the server job running. Save its ID and resume observation instead of submitting it again.

[Monitor jobs and handle failures](../jobs/) explains polling, progress callbacks, and retry behavior. Operators should use the separate [administrator client](../../operate/admin-cli/#use-an-administrator-client). Exact signatures and exception attributes are in the [Python reference](../../api/lyra/).
