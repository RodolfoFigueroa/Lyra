---
title: Monitor jobs and handle failures
description: Follow server jobs, respond to errors, and distinguish waiting from resubmission.
contentType: How-to
contentPlan: use/jobs
sidebar: { label: "Monitor Jobs And Handle Failures" }
---

A server job continues independently after submission. Keep its ID so you can check status, reconnect after a local interruption, and retrieve its result.

## Follow job status

Use `GET /jobs/{job_id}`, `client.jobs.get(job_id)`, or a job handle’s `status()` method.

| Status | What to do |
| --- | --- |
| `queued` | Wait for a worker to pick up the job |
| `running` | Continue checking; progress may be available |
| `succeeded` | Inspect and download the result |
| `failed` | Read the error before deciding whether to run again |

The status includes timestamps and the metric name, together with any available progress or error. Progress reflects the latest reported estimate, so a plugin can continue running even when it hasn’t published an update.

For HTTP, check roughly every five seconds. MCP observations provide their own two-second polling guidance.

## Wait with the Python client

`handle.wait(timeout=300)` checks immediately and then polls every five seconds, with small random timing variation. Change the cadence with a positive, finite `poll_interval`. `RunOptions` supports the same setting.

A finite timeout bounds polling and result retrieval. `timeout=None` waits indefinitely; zero expires immediately. Individual requests also have their own timeout. User callbacks and synchronous CPU work cannot be preempted by the wait deadline.

When waiting ends because of a timeout, interruption, or async cancellation, the remote job continues. There is no remote cancellation operation.

## Handle a failed job

A request can pass JSON Schema validation at submission and fail Python validation in the worker. For example, a cross-field validator may reject a combination that the JSON Schema permits. Such a job fails with `invalid_input` before its handler runs.

Other failures can come from source access, calculation errors, or invalid results. Graph execution stops at the failing node and publishes no partial outputs. Operators can inspect fuller diagnostics in [worker logs and administrator job details](../../operate/troubleshooting/).

The Python handle’s `wait()` and `result()` methods raise `MetricRunError` for failed jobs. The exception includes job context and the terminal result. `client.results.get(job_id)` lets you inspect a terminal result directly.

## Distinguish observation retries from new jobs

Every submission creates a new job. A submission timeout may happen after the server accepted it. Clients do not retry submissions automatically.

While waiting, the Python client retries connection failures, timeouts, and HTTP `429/500/502/503/504` responses. It permits five consecutive transient failures, resetting the count after a successful observation. Backoff starts at one second and grows to 30 seconds with jitter. Valid `Retry-After` guidance takes precedence.

Authentication errors, invalid payloads, certificate/configuration errors, and explicitly non-retryable service errors fail immediately. Exhausted observation retries raise `JobPollingError`; an expired deadline raises `JobWaitTimeoutError`. Neither submits another job.

## Handle connection and service errors

Client operations raise `DownloadError` for transport failures, unexpected statuses, malformed JSON, or invalid response models. You can inspect the chained cause to investigate the original failure.

A structured HTTP 503 raises `ServiceUnavailableError`, exposing `code`, `retryable`, and `retry_after_seconds`. Valid `Retry-After` values can be nonnegative seconds or an HTTP date. Missing or invalid guidance produces `None`. An unstructured 503 raises `DownloadError`. The readiness client accepts both 200 and 503 as valid health observations.

## Receive progress callbacks

Pass `on_progress` to `wait()` to receive the first available progress snapshot and subsequent content changes. Timestamp-only changes are ignored. Async handles await callbacks that return awaitables; callback exceptions propagate to the caller.

For server timeouts, retention, and worker interruption, read [the job lifecycle](../../concepts/lifecycle/). For completed data, continue to [results](../results/).
