# 2. Native HTTPX clients

Keep synchronous and native asynchronous consumer and administrator clients while
simplifying their transport and lifecycle. Both interfaces retain the same domain
operations and shared contracts, with ordinary HTTPX request behavior.

## Accepted scope

Back synchronous clients directly with HTTPX `Client` and asynchronous clients
with `AsyncClient`. Remove the background event-loop thread and the bridge that
runs synchronous calls through asynchronous machinery.

Share endpoint definitions, request construction, response validation, error
interpretation, and table conversion across both interfaces. Preserve the
currently supported consumer and administrator operations. Later result and
report endpoints must use these same shared responsibilities.

Group consumer job creation, execution, and observation under `client.jobs`.
Move `create()`, `submit()`, `run()`, and `run_to_file()` into that resource
alongside `get()`, and remove `client.raw` without compatibility aliases.
Preserve the method arguments, return types, and behavior in both client modes.

Replace generator-based polling with two ordinary loops sharing retry,
progress-comparison, and deadline helpers. Small amounts of duplication around
requests, sleeping, streaming, and context management are acceptable; eliminating
that duplication is not a reason to introduce another orchestration framework.

Use standard HTTPX connection, read, write, and pool timeouts. Polling deadlines
are checked between operations. An in-flight request or cleanup may exceed the
overall polling deadline, so the interface does not promise a strict total
wall-clock bound.

For example, a request started before the polling deadline may still be awaiting
its response when that deadline passes. Its HTTPX timeout and cleanup behavior
govern that in-flight operation; the polling loop checks its overall deadline
between operations. Do not recreate a background supervisor to impose a stricter
guarantee.

Asynchronous requests run in the caller's task and follow ordinary cancellation
and cleanup behavior. Callers finish outstanding operations, or cancel and await
them, before closing the client. Remove custom task tracking and background
cleanup ownership. Immediate return independent of cleanup completion is not a
guarantee, and local cancellation does not cancel a remote job.

## API sketches

These sketches retain the public imports and use `client.jobs` for consumer job
operations. They assume
`example_metric` is an installed metric accepting `{"value": 3}`; credentials are
placeholders. The proposed constructor contract for all four clients is
`timeout: float | httpx.Timeout | None = 30.0`: a number sets all four HTTPX
timeout categories, a `Timeout` configures them individually, and `None` disables
HTTP timeouts. Polling deadlines remain separate.

The consumer job methods accept a metric name and a JSON-compatible argument
dictionary. Each of the first four calls below submits a new job:

| Call | Result |
| --- | --- |
| `client.jobs.create("example_metric", {"value": 3})` | Submission response (`JobCreateResponse`). |
| `client.jobs.submit("example_metric", {"value": 3})` | Job handle for later observation and retrieval. |
| `client.jobs.run("example_metric", {"value": 3})` | Successful result after waiting (`TableJobResult`). |
| `client.jobs.run_to_file("example_metric", {"value": 3}, path="result.json")` | Saves the result after waiting; returns `None`. |
| `client.jobs.get(job_id)` | Current status of an existing job (`JobStatusInfo`). |

### Synchronous consumer

```python
import httpx

from lyra.api import LyraClient

with LyraClient(
    "lyra.example.com",
    agent_api_key="agent-key",
    headers={"X-Request-Source": "example"},
    timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
) as client:
    metrics = client.catalog.metrics(query="example", limit=10)
    handle = client.jobs.submit("example_metric", {"value": 3})
    current_status = client.jobs.get(handle.job_id)
    status = handle.wait(timeout=300, poll_interval=1.0, on_progress=print)
    result = handle.result()
    client.results.download(handle.job_id, "result.json")
    tables = client.result_tables(handle.job_id)
```

`wait()` returns successful `JobStatusInfo` without fetching output data;
`result()` returns `TableJobResult`. Table conversion retains its optional
dependencies and named DataFrame/GeoDataFrame mapping. Downloads stream to the
destination and close the response before returning. Hosts omit the URL scheme;
`secure=False` selects HTTP. Explicit `client.close()` in a `finally` block is
also supported. Job handles require their client to remain open.

### Asynchronous consumer and run options

```python
from lyra.api import AsyncLyraClient, RunOptions

async def save_result() -> None:
    async with AsyncLyraClient(
        "lyra.example.com", agent_api_key="agent-key", timeout=30.0
    ) as client:
        await client.jobs.run_to_file(
            "example_metric",
            {"value": 3},
            path="result.json",
            options=RunOptions(timeout=300, poll_interval=1.0),
        )
```

Other consumer calls use the same arguments with `await`, including
`await client.jobs.create(...)`, `await client.jobs.submit(...)`,
`await client.jobs.get(job_id)`, `await handle.wait(...)`,
`await handle.result()`, and `await client.result_tables(...)`. For an in-memory
result, use `await client.jobs.run(...)` with the same run options. Both run
helpers submit a new job. Synchronous clients expose these helpers without
`await`. Progress callbacks receive changed snapshots; asynchronous waits accept
ordinary or awaitable callbacks, and callback failures propagate.

`RunOptions.timeout` starts after submission and covers polling plus final result
retrieval, with deadline checks between operations. `handle.wait(timeout=...)`
covers only that wait; a later result request has its own HTTP timeout. Both
polling options default to no overall deadline (`None`). A request or cleanup
already in progress can outlast the deadline. HTTPX read timeouts apply to waiting
for chunks, so a progressing download can take longer than the configured read
timeout.

### Administrator clients

```python
from lyra.api import AsyncLyraAdminClient, LyraAdminClient

with LyraAdminClient("lyra.example.com", admin_api_key="admin-key") as admin:
    status = admin.status()
    workers = admin.workers.list()

async def inspect_service() -> None:
    async with AsyncLyraAdminClient(
        "lyra.example.com", admin_api_key="admin-key"
    ) as admin:
        status = await admin.status()
        workers = await admin.workers.list()
```

Administrator clients use the same timeout and closure rules, with separate
administrator credentials and resources.

### Wait failures and cancellation

For a submitted handle whose client is still open:

```python
from lyra.api import JobWaitTimeoutError, MetricRunError

try:
    handle.wait(timeout=10)
except JobWaitTimeoutError as exc:
    print("Observe this job later:", exc.job_id)
except MetricRunError as exc:
    print("Job failed:", exc.job_id, exc.error)
```

Transport failures retain Lyra's `DownloadError` contract, with the HTTPX cause
chained. Structured service failures retain `ServiceUnavailableError`. Ordinary
requests and submissions are not automatically retried; polling retains bounded
retries and raises `JobPollingError` on exhaustion. A failed submission may have
been accepted remotely, so repeating it may create another job.

The caller owns concurrent operations and awaits their completion before closing
the client. For example, `stop` below is an application-owned `asyncio.Event`:

```python
import asyncio
from contextlib import suppress

from lyra.api import AsyncLyraClient

async def run_until_stopped(stop: asyncio.Event) -> None:
    async with AsyncLyraClient(
        "lyra.example.com", agent_api_key="agent-key"
    ) as client:
        task = asyncio.create_task(client.jobs.run("example_metric", {"value": 3}))
        try:
            await stop.wait()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
```

Cancellation propagates through the local operation and its cleanup. Awaiting the
cancelled task may take time; neither cancellation nor a wait timeout cancels the
remote job. `aclose()` releases resources after the caller has settled its tasks.
Explicit `await client.aclose()` in a `finally` block is also supported.

## Completion boundary

Both client interfaces remain usable for existing consumer and administrator
workflows, with equivalent request, response, error, and table semantics. Their
documented lifecycle matches native HTTPX behavior.

Both consumer clients expose submission, execution, and status lookup through
`client.jobs`. Relevant tests, examples, and client guidance reflect this public
contract.

Isolated checks cover both interfaces, polling success and failure, retries,
deadline handling, streaming and closure, and asynchronous cancellation. Tests
must not depend on a real HTTP service or require the removed background-thread
organization.

## Deferred or excluded work

New Arrow, paginated-row, and execution-report access arrives in
[milestone 11](11-result-and-report-retrieval.md). This milestone provides their
transport foundation without adding those endpoints early. Shared job-ID validation
and result lookup semantics belong to [milestone 3](03-mcp-and-shared-references.md).

Strict total-duration guarantees, custom cancellation ownership, and automatic
remote-job cancellation are outside the accepted client contract.

## Reference points

The current client separates the [synchronous bridge](../src/lyra/api/client/bridge.py),
[transport lifecycle](../src/lyra/api/client/http.py), and
[polling protocol](../src/lyra/api/client/polling.py).
HTTPX documents [native asynchronous clients](https://www.python-httpx.org/async/)
and its [timeout categories](https://www.python-httpx.org/advanced/timeouts/).
