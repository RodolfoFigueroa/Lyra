# Phase 27 — Remote acceptance and persistence

Read the [shared workflow](index.md). Continue after
[phase 26](26-worker-bound-plan-execution.md). Apply ledger decisions **10, 11, 16, 21, 22, 26, 27**.

Use shared preparation at remote acceptance and durably preserve its bindings for
the worker.

## Work

- Connect remote submission to the same catalog-based preparer used locally.
  Resolve visibility, edition, parameters, source contracts, routes, and revisions
  before creating or enqueueing a job.
- Persist the accepted plan, original validated selections, and result metadata
  using the existing job storage/enqueue lifecycle. Ensure the worker receives the
  captured bindings rather than reconstructing them from the original request.
- Keep acquisition inside worker execution. Submission does not resolve IDs,
  query providers, or instantiate source clients.
- Preserve existing job ownership, queue selection, handles, and lifecycle
  behavior while adapting concrete metric/edition identity and provenance.
- Distinguish preparation rejection, submission infrastructure failure, and
  execution failure after acceptance. Unknown IDs and unavailable pinned data at
  execution become job failures, not silently altered requests.

## Acceptance and validation

- With fake storage and queues, prove invalid requests create no job and enqueue
  nothing. Verify valid acceptance acquires no source data.
- Retrieve an accepted plan from storage and execute it with the phase 26 worker
  component. Cover edition-specific binding, multi-input routes, and revisions.
- Change catalog/revision metadata between acceptance and execution; verify the
  captured bindings are honored or the job fails clearly without replanning.
- Test persistence/enqueue failures using the existing lifecycle guarantees and
  confirm terminal execution failures retain accepted-plan context.
- Run the index's Python checks and submission/store/worker tests. Measure
  acceptance and worker execution as distinct benchmark operations.

## Boundary

Do not redesign queue infrastructure or job administration. REST, Python client,
and MCP presentation of these semantics follow in their respective phases.
