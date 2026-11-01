# Phase 18 — Canonical route execution

Read the [shared workflow](index.md). Continue after
[phase 17](17-pinned-location-acquisition.md). Apply ledger decisions **3, 4, 7–9, 16–18**.

Execute the bound canonical route for each public selection and deliver native
location inputs through the existing executor.

## Work

Use `LocationResolver.resolve_prepared` inside the execution-owned resource scope
for source acquisition. `GraphExecutor.acquire_sources` provides standalone scoped
acquisition and checks operation revisions; route execution must retain that
revision check while keeping the scope open through selectors and the metric.

- Resolve initial source selections, then invoke the exact bound operation
  sequence. Bind each hop explicitly to the preceding result. A zero-hop route
  supplies acquired native locations directly.
- Use ordinary invocation machinery and the same resource scope for selectors,
  their dependencies, and the metric. Preserve canonical operations' declared
  defaults and fixed bindings rather than inheriting metric parameters by name.
- Pass batches to handlers and produce the union of related targets, once per
  target identity in deterministic order. Validate complete target rows under the
  declared contract. Do not force per-ID execution or a second acquisition query.
- Pass valid empty intermediate selections onward, including to downstream
  selectors and metrics. Let handlers determine counts, summaries, and multi-input
  results rather than fabricating an empty metric result.
- Complete native input resolution before entering the metric's invocation/cache
  path. Keep calculation dependencies and output granularity unchanged.
- Never switch routes after failure or because another route has warmer caches.

## Acceptance and validation

- Exercise multi-hop routes, native selections, shared selectors, multiple inputs,
  overlapping target sets, complete target attributes, and empty intermediates.
- Verify batched invocation, deterministic unique output, resource reuse, and no
  mandatory target reacquisition using fake-provider and handler counters.
- Make a selected route fail while an alternative would succeed; assert the bound
  route remains authoritative. Preserve ordinary dependency behavior throughout.
- Run the index's Python checks and execution tests. Measure native/routed and
  batched/multi-input execution separately from preparation.

## Boundary

Do not add grouping, inferred source-to-target membership, or alternative routing
syntax. Calculations needing source-to-target membership must preserve it
explicitly. Phase 19 deepens binding and combined-plan validation; phases 20–21
complete revision-aware caching and reuse across canonical selections.
