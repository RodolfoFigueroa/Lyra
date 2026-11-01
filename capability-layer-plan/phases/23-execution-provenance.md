# Phase 23 — Execution provenance

Read the [shared workflow](index.md). Continue after
[phase 22](22-immutable-metadata-reuse.md). Apply ledger decisions **16, 18, 22, 26, 27**.

Retain execution provenance in the shared core before presenting it through
local or remote APIs.

## Work

- Preserve validated original selections, effective parameters, concrete metric
  and contract identity, edition, per-input routes, pinned revisions including
  absence, run-wide revision, and native-input counts/content fingerprints.
- Carry relevant source, period, release, field definitions, and output meaning
  alongside the accepted plan.
- Distinguish planned dependencies from observed handler execution and cache
  reuse. Capture observations at the executor's real invocation/cache boundaries,
  including shared dependency reuse, without adding a timing-trace system.
- Construct current-run provenance when cached native outputs are reused. Do not
  report unvisited calculation dependencies as executed merely because they were
  present in the plan.
- Preserve accepted context and observed execution context when execution fails,
  without embedding intermediate tables or geometries.

## Acceptance and validation

- Compare cold execution, selector cache hits, root cache hits, and shared
  dependency reuse. Verify planned but unvisited nodes are not marked executed.
- Reach the same cached native result from different original selections and
  check each run retains its own routes and pinned revisions.
- Test missing/declared revisions, empty native inputs, source/release metadata,
  and a failure after some dependencies complete.
- Run the index's Python checks and executor/result/cache tests. Measure
  representative provenance construction and retention costs.

## Boundary

Keep this component independent of remote job envelopes and presentation layers.
The next phase exposes separate local inspection. Do not add replayable snapshots,
per-node timing traces, or an MCP execution explorer.
