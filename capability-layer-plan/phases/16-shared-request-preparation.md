# Phase 16 — Shared request preparation

Read the [shared workflow](index.md). Continue after
[phase 15](15-revision-planning.md). Apply ledger decisions **10, 11, 15, 16, 21, 22, 26**.

Prepare a complete public execution binding from catalog metadata before any
source acquisition or remote job creation.

## Work

- Define the common request contract: public metric, edition when required,
  ordinary parameters, named collection/unit-type ID selections, and optional
  run-wide cache revision. Do not accept operation names or caller pipelines.
- Validate public visibility, explicit versus implicit edition selection, input
  names, nonempty ID selections, accepted source types, and the complete parameter
  schema. Compute effective parameter values including defaults.
- Bind the concrete metric and source contracts, exact canonical route per input,
  effective parameters, relevant revision values, and computation identity.
  Preserve validated original selections separately from eventual native inputs.
- Own or freeze prepared state so later caller mutation cannot alter the accepted
  plan. Make preparation usable by local and remote callers without requiring
  handler execution or resource construction.
- Establish shared semantic preparation errors with useful input and edition
  context. Keep unknown source IDs for execution-time resolution.

## Acceptance and validation

- Cover native and routed requests, multiple inputs, defaults, a single named
  edition, implicit editions, private metrics, unknown fields, invalid parameters,
  empty selections, and unsupported source types.
- Use acquisition and enqueue spies to prove successful preparation and all
  rejected requests perform neither operation.
- Check that catalog or caller changes after preparation do not mutate captured
  bindings. Verify missing revisions remain explicitly unversioned.
- Run the index's Python checks and preparation tests. Measure preparation
  separately from acquisition and execution, including rejection workloads.

## Boundary

Implement the shared preparation component and necessary existing callers. Complete
public local integration in phase 24 and durable remote acceptance in phase 27;
do not implement separate preparation logic for those interfaces.
