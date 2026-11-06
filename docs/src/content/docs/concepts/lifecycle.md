---
title: How jobs execute and expire
description: Understand background execution, optional progress, worker interruption, and result expiration.
contentType: Conceptual
contentPlan: concepts/lifecycle
sidebar: { label: "Job Lifecycle And Retention" }
---

A server submission creates an independent background job that continues after the client’s request returns. The server manages execution and result retention; the client observes these stages through the job identifier (ID):

```text
queued → running → succeeded
                 → failed
```

Polling reads the job’s current status; it does not drive execution.

## Submission captures the request

The application programming interface (API) server validates your request against the selected family and edition. Before queueing the job, it applies parameter defaults and saves the execution bindings:

- The concrete metric node
- The canonical route for each named input
- Pinned location revisions, which fix the requested data versions

Workers use these bindings and resolve IDs inside a [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials), which manages data clients during execution. A worker fails the job if it cannot honor a binding; it does not choose a new plan.

[Provenance](../../use/results/#inspect-execution-provenance) records the request and execution history. It distinguishes your original selections and submitted parameters from effective parameters, which include defaults. Execution records observed native inputs and executed or reused nodes separately from the accepted plan. Pinning metadata does not create a storage snapshot or detect unreported source changes.

Every submission creates a new job. There is no automatic deduplication or submission retry. After a transport timeout, you may not know whether the server accepted your job.

## Execution has its own deadline

Queued jobs do not expire because of queue age. Running jobs use the Redis Queue (RQ) execution timeout. Set it with `jobs.execution_timeout_seconds`; the default is 18000s (five hours).

The worker checks plugin definitions against the accepted contract, executes the calculation, and validates its output tables. Python validation can reject a request that passed the API’s JavaScript Object Notation (JSON) Schema check.

Progress reports describe the latest available estimate and may be absent throughout a calculation. Use lifecycle status to determine whether the job has finished, even when no progress updates appear.

## Client waiting is separate

A client’s wait deadline limits how long it observes a job. Timing out, interrupting a synchronous wait, or cancelling an asynchronous wait leaves the server job running.

Resume observation using the saved job ID. A second submission creates another execution. The consumer interface has no remote cancellation operation.

[Job handling](../../use/jobs/) explains polling and error recovery.

## RQ detects worker failure

RQ supervises execution, tracks worker heartbeats, and detects failures. Worker maintenance detects abandoned jobs, although outages or saturation can delay that check. API reads do not clean up or repair records.

Execution failure does not automatically replay the job. Operators inspect the failed job and source condition before deciding whether to request new work.

## Completed data has a retention period

Successes and failures remain available for `jobs.result_retention_seconds`, which defaults to 86400s (one day). [Result inspections](../../use/results/#inspect-before-downloading) report the remaining lifetime.

Retention controls Redis records and API access. [Offline pruning](../../operate/caching/#reclaim-artifact-storage) removes output files that no retained job or computation lookup references. Operators clean up temporary scratch files separately.

An unavailable reference does not tell you whether the job never existed or its retention period ended. Download data you need before it expires. See [results](../../use/results/) for downloads and [monitoring](../../operate/runbook/) for operational settings.
