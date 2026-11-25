---
title: How jobs execute and complete
description: Understand background execution, worker failures, and access to saved results after queue expiry.
contentType: Conceptual
contentPlan: concepts/lifecycle
sidebar: { label: "Job Lifecycle And Retention" }
---

A server submission creates an independent background job that continues after the client’s request returns. The server manages execution and result retention; you can use the job ID to check its status:

```text
queued → running → succeeded
                 → failed
```

Polling reads the job’s current status; it does not drive execution.

A job ID contains exactly 32 lowercase hexadecimal characters. The same ID identifies the queue record, every node context, and the retained report and tables.

## Submission captures the request

The API server validates your request against the selected metric. Before queueing the job, it applies parameter defaults and saves these execution bindings:

- The concrete metric node
- The [canonical route](../selection/) for each named input
- Pinned location revisions, which fix the requested data versions

When the worker receives a job, it checks whether its current definitions can execute the plan the API accepted.

1. The worker matches the accepted job ID to the queue record.
2. It prepares the request using its own definitions and checks the effective parameter values.
3. It checks the selected computation and routes, including their provider scopes and interventions.

The worker can still run the job if you update an unrelated plugin. If you change a selected dependency’s revision, the plans disagree. The worker reports `bound_plan` for an incompatible plan before opening resources or running handlers.

After validation, the worker resolves your selected IDs inside a [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials), which manages data clients.

[Provenance](../provenance/) records your selections and effective parameters, including defaults and nulls. It separates the accepted request from observations of resolved inputs and calculations that ran or reused cached results. Your plugin must honor the captured identities; Lyra can’t detect source changes that its declarations omit.

Every submission creates a new job. There is no automatic deduplication or submission retry. After a transport timeout, you may not know whether the server accepted your job.

## Execution has its own deadline

Jobs can wait in the queue indefinitely. Once a worker starts a job, Redis Queue (RQ) enforces the execution timeout. Set `jobs.execution_timeout_seconds` to change the default of 18000s (five hours).

During execution, the worker runs the calculation and validates its output tables. The API and worker use the same Pydantic parameter models to validate your request.

The worker commits the result description and [execution report](../provenance/) together in SQLite. You can then read them by job ID, even if the worker stops before RQ records success.

If storage or publication fails, the job fails even when calculation finished. Python job-handle result and wait methods raise `MetricRunError`. Complete cache values saved earlier can remain reusable after a later publication failure.

Progress reports describe the latest available estimate and may be absent throughout a calculation. Use lifecycle status to determine whether the job has finished, even when no progress updates appear.

## Client waiting is separate

A client’s wait deadline limits how long it observes a job. Timing out, interrupting a synchronous wait, or cancelling an asynchronous wait leaves the server job running.

Resume observation using the saved job ID. A second submission creates another execution. The consumer interface has no remote cancellation operation.

[Job handling](../../use/jobs/) explains polling and error recovery.

## RQ detects worker failure

RQ supervises execution, tracks worker heartbeats, and detects failures. Worker maintenance detects abandoned jobs, although outages or saturation can delay that check. API reads do not clean up or repair records.

Execution failure does not automatically replay the job. Operators inspect the failed job and source condition before deciding whether to request new work.

## Terminal job history

SQLite retains successful outcomes and structured failures indefinitely. Look up a completed job by ID or list terminal jobs by status in the admin CLI. Both work during a Valkey outage.

RQ retains native job records and admin tracebacks for `jobs.rq_retention_seconds`, which defaults to one day. Queued jobs, running jobs, progress, and new submissions require Valkey. If SQLite is temporarily unavailable when a worker records a failure, worker maintenance retries while the RQ failure record remains available.
