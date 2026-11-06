---
title: Monitor jobs and handle failures
description: Follow server jobs, respond to errors, and distinguish waiting from resubmission.
contentType: How-to
contentPlan: use/jobs
sidebar: { label: "Monitor Jobs And Handle Failures" }
---

A server job continues independently after submission. Keep its identifier (ID) to check status and retrieve the result, including after a local interruption.

## Follow job status

Check status through `GET /jobs/{job_id}`, `client.jobs.get(job_id)`, or a job handle’s `status()` method. Use the returned status to choose your next action:

| Status | What to do |
| --- | --- |
| `queued` | Wait for a worker to pick up the job |
| `running` | Continue checking; progress may be available |
| `succeeded` | Inspect and download the result |
| `failed` | Read the error before deciding whether to run again |

The status includes timestamps and the metric name, together with any available progress or error. Progress reflects the latest reported estimate, so a plugin can continue running even when it hasn’t published an update.

You can still check a completed job’s status if the server retains the job but its result is missing or invalid. Each response reflects the status when the server reads it; check again to see later changes.

For result requests, use the Hypertext Transfer Protocol (HTTP) response to choose your next action:

| Job and result state | Response | What to do |
| --- | --- | --- |
| Queued or running | `202` with current status; inspections also include a result reference and polling guidance | Wait, then check again |
| Succeeded with a valid result | `200` with the result or inspection | Use the result |
| Failed | `200` with failure details or an inspection | Read the error before deciding whether to run again |
| Succeeded, but the result is missing | `404` | Treat the result as unavailable; you can still check job status |
| Succeeded, but the result is invalid | `500` with a generic error message and `retryable: false` | Stop retrying; you can still check job status |
| Job absent or expired | `404` | Stop checking; status is also unavailable |

Downloads include `Content-Disposition: attachment` only after the job succeeds or fails. Pending responses contain status information without an attachment header.

For HTTP, check roughly every five seconds. Model Context Protocol (MCP) observations include two-second polling guidance.

## Wait with the Python client

`handle.wait(timeout=300)` checks immediately and then polls every five seconds, with small random timing variation. Change the cadence with a positive, finite `poll_interval`. `RunOptions` supports the same setting.

While a job is active, `client.results.inspect()` returns its status and polling guidance. Full-result methods and `handle.result()` raise `JobNotReadyError` until the job completes. Import it from `lyra.api` and inspect `job_id` and `snapshot` for the job’s identity and current status. A pending download leaves your existing destination file untouched.

Both synchronous and asynchronous `wait()` methods handle pending responses automatically. They report changed progress, reset the count of consecutive transient failures, and continue at your configured polling interval.

A finite `wait()` timeout bounds status polling; `wait()` returns status without fetching the result. `timeout=None` waits indefinitely, while zero expires immediately. Each request also has its own timeout.

For `run()` and `run_to_file()`, the [run deadline](../python-client/#manage-timeouts-and-cleanup) also covers the final result transfer. Deadlines cannot interrupt callbacks or synchronous processing.

When waiting ends because of a timeout, interruption, or async cancellation, the remote job continues. There is no remote cancellation operation.

## Handle a failed job

A request can pass JavaScript Object Notation (JSON) Schema validation at submission and fail Python validation in the worker. For example, a cross-field validator may reject a combination that the JSON Schema permits. Such a job fails with `invalid_input` before its handler runs.

Other failures can come from source access, calculation errors, or invalid results. Graph execution stops at the failing node and publishes no partial outputs. Operators can inspect fuller diagnostics in [worker logs and administrator job details](../../operate/troubleshooting/).

The Python handle’s `wait()` and `result()` methods raise `MetricRunError` for failed jobs. The exception includes the job ID and structured error. `client.results.get(job_id)` lets you inspect a terminal result directly.

## Distinguish observation retries from new jobs

Every submission creates a new job. A submission timeout may happen after the server accepted it. Clients do not retry submissions automatically.

While waiting, the Python client retries these transient failures:

- Connection failures
- Request timeouts
- HTTP `429`, `500`, `502`, `503`, and `504` responses

It permits five consecutive transient failures and resets the count after a successful observation. Delays start at 1s and grow to 30s, with random variation to avoid synchronized retries. Valid `Retry-After` guidance takes precedence.

Authentication errors, invalid payloads, certificate/configuration errors, and explicitly non-retryable service errors fail immediately. Exhausted observation retries raise `JobPollingError`; an expired deadline raises `JobWaitTimeoutError`. Neither submits another job.

## Handle connection and service errors

Client operations raise `DownloadError` for transport failures, unexpected statuses, malformed JSON, or invalid response models. You can inspect the chained cause to investigate the original failure.

A structured HTTP `503` raises `ServiceUnavailableError` with error details:

- `code`: the service error identifier
- `retryable`: whether to retry the operation
- `retry_after_seconds`: how long to wait before retrying

Valid `Retry-After` values can be nonnegative seconds or an HTTP date. Missing or invalid guidance produces `None`. An unstructured `503` raises `DownloadError`. The readiness client accepts both `200` and `503` as valid health observations.

## Receive progress callbacks

Pass `on_progress` to `wait()` to receive the first available progress snapshot and subsequent content changes. The client ignores changes to timestamps alone. Async handles await callbacks that return awaitables; callback exceptions propagate to the caller.

For server timeouts, retention, and worker interruption, read [the job lifecycle](../../concepts/lifecycle/). For completed data, continue to [results](../results/).
