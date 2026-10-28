---
title: Job lifecycle and retention
description: Understand background execution, optional progress, worker interruption, and result expiration.
contentType: Conceptual
contentPlan: concepts/lifecycle
sidebar: { label: "Job Lifecycle And Retention" }
---

A server submission creates an independent background job. Its lifecycle continues after the client’s request returns.

```text
queued → running → succeeded
                 → failed
```

The client observes that lifecycle through the job ID. It does not drive execution by polling.

## Submission captures the request

The API validates the public schema and resolves spatial selectors. It records the unresolved request provenance and contract identity, then queues resolved inputs for a worker.

Defaults are applied during worker model construction. Omitting a field and explicitly submitting its default remain distinct in the captured provenance.

Every submission creates a new job. There is no automatic deduplication or submission retry. A transport timeout can therefore leave the caller uncertain whether a job was accepted.

## Execution has its own deadline

Queued jobs do not expire because of queue age. Once running, they use the Redis Queue (RQ) execution timeout, configured by `jobs.execution_timeout_seconds` (default 18000 seconds).

The worker validates imported definitions and submitted contract identity, executes the graph, and validates its native results. Python semantic validation can fail a request that passed the API’s JSON Schema check.

Progress reports describe the latest available estimate and may be absent throughout a calculation. Use lifecycle status to determine whether the job has finished, even when no progress updates appear.

## Client waiting is separate

A client’s wait deadline limits how long it observes a job. Timing out, interrupting a synchronous wait, or cancelling an asynchronous wait leaves the server job running.

Resume observation using the saved job ID. A second submission creates another execution. The consumer interface has no remote cancellation operation.

[Job handling](../../use/jobs/) explains polling and error recovery.

## RQ detects worker failure

RQ owns execution supervision, heartbeats, and failure detection. Worker maintenance detects abandoned jobs, although outages or saturation can delay that check. API reads do not clean up or repair records.

Execution failure does not automatically replay the job. Operators inspect the failed job and source condition before deciding whether to request new work.

## Completed data has a retention period

Successes and failures remain available for `jobs.result_retention_seconds` (default 86400 seconds). Descriptors expose the remaining lifetime.

Retention governs Redis records and API access. It does not clean scratch files from disk. An unknown or expired reference has no tombstone that proves which case occurred.

Download data you need before it expires. See [results](../../use/results/) for downloads and [monitoring](../../operate/runbook/) for operational settings.
