# Phase 26 — Worker bound-plan execution

Read the [shared workflow](index.md). Continue after
[phase 25](25-bound-plan-serialization.md). Apply ledger decisions **16, 18, 22, 26, 27**.

Execute a serialized accepted plan with available worker implementations, or
fail clearly when the worker cannot honor it.

## Work

- Resolve persisted concrete operation and producer identifiers to worker
  implementations. Validate captured contracts, computation identity, and revision
  support before using them for execution or caching.
- Invoke the shared executor with the accepted metric, effective parameters,
  source bindings, and exact route sequences. Never reconstruct the plan from the
  original request using the current catalog.
- Reject unavailable implementations or pinned data instead of substituting a
  newer revision or different route. Historical data retention is not required.
- Reuse the core provenance and failure machinery, keeping accepted bindings
  distinct from observed execution and cache reuse.
- Test the worker component with serialized plans and fake job/resource objects.
  Keep any changed producer/consumer boundary coherent; full acceptance and
  persistence wiring belongs to the next phase.

## Acceptance and validation

- Execute a serialized plan and compare its outputs and binding interpretation
  with equivalent core/local execution.
- Change routes, contracts, defaults, or revisions between preparation and worker
  execution. Honor captured bindings when supportable; otherwise report failure
  without replanning or caching newer data under an earlier identity.
- Cover missing implementations, unavailable revisions, resource cleanup, failed
  jobs, and current-run provenance on native cache hits.
- Run the index's Python checks and affected worker/execution/result tests with
  fake services and temporary artifacts.

## Boundary

Do not redesign queues, create snapshots, or add compatibility envelopes. Durable
acceptance before enqueueing is completed in
[remote acceptance and persistence](27-remote-acceptance-and-persistence.md).
