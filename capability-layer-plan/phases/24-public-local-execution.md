# Phase 24 — Public local execution

Read the [shared workflow](index.md). Continue after
[phase 23](23-execution-provenance.md). Apply ledger decisions **10, 11, 15, 16, 26, 27**.

Complete the local public interface using the already-tested preparer, executor,
cache, and provenance components.

## Work

- Connect ordinary local calls to shared preparation and canonical execution.
  Accept public metrics, required editions, ordinary parameters, named source ID
  selections, and optional cache revision.
- Return native named tables. Provide separate inspection of bound metadata and
  the shared execution provenance without imposing a remote job envelope.
- Retain deliberate development/test access to private metrics and noncanonical
  graphs outside the ordinary public request contract.
- Preserve actionable preparation versus execution errors, including input,
  source scope, and unavailable-revision context.
- Update affected local examples and callers directly. This phase composes
  established core behavior rather than rebuilding routing or provenance.

## Acceptance and validation

- Exercise native and multi-hop local calls, multiple inputs, implicit and named
  editions, private intermediates, and public rejection of private roots.
- Confirm ordinary callers cannot provide operation names or custom pipelines,
  and that required editions and parameter constraints are enforced before access.
- Cover empty intermediate selections, unknown IDs, unavailable revisions, native
  table returns, and separate current-run inspection on a cache hit.
- Run the index's Python checks and local integration tests with fake resources.
  Measure representative local calls against the previous comparable checkpoint.

## Boundary

Do not introduce new dependency semantics or a local job service. Serialization
and worker execution of accepted remote bindings follow in their own phases.
