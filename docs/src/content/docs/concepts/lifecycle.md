---
title: How jobs execute and expire
description: Understand background execution, optional progress, worker interruption, and result expiration.
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

Shared job models require a nonempty string for each job ID and reject other values without conversion. They preserve every character, including whitespace, and accept strings that contain only whitespace. Job IDs have no required format. A valid ID doesn’t guarantee that the job exists.

## Submission captures the request

The API server validates your request against the selected metric. Before queueing the job, it applies parameter defaults and saves these execution bindings:

- The concrete metric node
- The [canonical route](../selection/) for each named input
- Pinned location revisions, which fix the requested data versions

Before acquiring resources, the worker checks your job in this order:

1. Compare the accepted [build revision](../../operate/configuration/#assign-a-deployment-build-revision) with the worker’s revision
2. Prepare the request using the worker’s plugin definitions and revalidate its effective parameters with Pydantic
3. Run the executor’s plan validation before loading data or reusing cached results

If the build revisions differ, the worker fails the job with `deployment_changed` before acquiring resources. Submit a new job to use the current deployment.

After validation, the worker resolves your selected IDs inside a [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials), which manages data clients. The worker fails the job if it cannot honor an accepted binding.

[Provenance](../provenance/) records your selections and effective parameters, including defaults and nulls. It separates the accepted plan from observations of resolved inputs and calculations that ran or reused cached results. Pinning metadata does not create a storage snapshot or detect unreported source changes.

Every submission creates a new job. There is no automatic deduplication or submission retry. After a transport timeout, you may not know whether the server accepted your job.

## Execution has its own deadline

Queued jobs do not expire because of queue age. Running jobs use the Redis Queue (RQ) execution timeout. Set it with `jobs.execution_timeout_seconds`; the default is 18000s (five hours).

During execution, the worker runs the calculation and validates its output tables. The API and worker use the same Pydantic parameter models to validate your request.

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
