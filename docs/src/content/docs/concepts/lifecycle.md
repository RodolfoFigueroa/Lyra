---
title: How jobs execute and expire
description: Understand background execution, worker failures, and access to saved results after job expiry.
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

Before acquiring resources, the worker checks your job in this order:

1. Match the accepted job ID to the queue record and compare the accepted [build revision](../../operate/configuration/#assign-a-deployment-build-revision) with the worker’s revision
2. Prepare the request using the worker’s plugin definitions and revalidate its effective parameters with Pydantic
3. Run the executor’s plan validation before loading data or reusing cached results

If the build revisions differ, the worker fails the job with `deployment_changed` before acquiring resources. Submit a new job to use the current deployment.

After validation, the worker resolves your selected IDs inside a [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials), which manages data clients. The worker fails the job if it cannot honor an accepted binding.

[Provenance](../provenance/) records your selections and effective parameters, including defaults and nulls. It separates the accepted request from observations of resolved inputs and calculations that ran or reused cached results. Your plugin must honor the captured identities; Lyra can’t detect source changes that its declarations omit.

Every submission creates a new job. There is no automatic deduplication or submission retry. After a transport timeout, you may not know whether the server accepted your job.

## Execution has its own deadline

Queued jobs do not expire because of queue age. Running jobs use the Redis Queue (RQ) execution timeout. Set it with `jobs.execution_timeout_seconds`; the default is 18000s (five hours).

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

## Job access has a retention period

Successes and failures remain available for `jobs.result_retention_seconds`, which defaults to 86400s (one day). [Result inspections](../../use/results/#inspect-before-downloading) report the remaining lifetime.

When the Redis record expires, a status lookup returns HTTP 404. Failure details and job-based JSON downloads also expire.

Keep your submission’s job ID to read [saved reports, descriptions, and named tables](../../use/results/) after queue expiry. Use the API for remote reads or open the store file with `ResultStore`. Operators can check the [retention settings](../../operate/runbook/).
