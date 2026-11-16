# Phase 6: Partial reuse and group assembly

## Objective and prerequisites

Retain the complete logical inputs of a result assembled from cached and fresh
metric computations. Complete [phase 5](05-complete-cache-reuse.md) and read its
handoff plus the selection representation from phase 2. Follow the
[overview](index.md), [milestone 6](../feature-milestones/06-unified-graph-execution.md),
[milestone 9](../feature-milestones/09-node-caching.md), and the mixed-reuse examples
in [milestone 12](../feature-milestones/12-retained-location-inputs.md).

Use the same reference representation and publication boundary as complete hits.
This phase completes runtime assembly, not a replacement of the preceding work.

## Implementation

Combine reconstruction references from each reused computation with references
captured for new work. For a request A/B/C with A/B cached and only C calculated,
the primary input read returns A/B/C. Do not save only the last invocation's frame
or reconstruct inputs from metric output rows, since an input can yield no output.

Apply existing binding relationships when choosing each computation's inputs:

- Primary and partition-related selections follow the relevant requested groups.
- Independent direct or operation-derived references retain their complete shared
  context, including repeated targets belonging to different immediate sources.
- A selected group from a `full` or uncached upstream computation retains that
  computation's identity while exposing only the selected input groups/pairs.
- Unpartitioned dependencies keep their complete bound selection where required.
- Provider or bare-ID coincidence does not establish a partition relationship.
  Use explicit binding lineage and milestone 6's supported matching rules.

Account for source and target restrictions when an operation-derived primary
selection has several incoming pairs. Deduplicate identical direct records and
identical qualified pairs across fragments, while preserving `(A, T)` and `(B, T)`
as distinct rows. A multi-hop input still reports final-hop pairs, never expanded
ancestral relationships. No row order is promised.

Share large reference selections by retained description/reference. Do not embed
the same X/Y membership list in every primary computation or assembled result.
Retain only required groups from broader computations, and do not materialize
joined input geometry while assembling result metadata or cache-hit publication.

Preserve reduced acquisition and existing provider/operation cache policies.
Fresh work receives only its needed related rows plus complete shared context;
input inspection must not cause fresh acquisition for cached portions. Missing
required retained references are integrity failures, not a reason to rerun those
partitions. Required persistence failure for a new fragment prevents successful
publication of the final result, while independently completed values can survive.

## Starting points

- [Bound selection and demand](../src/lyra/runtime/planning/bindings.py), especially
  related selections and computation-key derivation.
- [Native related selections](../src/lyra/runtime/native_inputs.py) and
  [executor assembly](../src/lyra/runtime/execution.py), including metric dependency
  preparation, completion tracking, and both reuse paths.
- [Output selections](../src/lyra/runtime/output_selection.py) and the input
  selection/reference implementation from phases 2–5.

## Verification and completion

Build a behavioral matrix around these scenarios:

| Scenario | Required observation |
| --- | --- |
| A/B cached, request A/B/C | Only C requires metric computation; input read includes A/B/C. |
| Shared X/Y reference with many partitions | Every computation sees complete context; retrieval contains X/Y once. |
| Full upstream A/B/C used for A/B | C's unrelated groups never appear in the retained input. |
| A and B both select T | Both immediate pairs survive assembly. |
| A → P → T across fresh and cached fragments | Final input retains parcel/tree roles and final-hop pairs. |
| No findings for a requested group | Its supplied location inputs remain retrievable. |
| Changed provider/code/origin scope with equal bare IDs | Distinct identities never merge. |
| Different selections of one metric within a graph | Each use retains its own complete logical binding. |

Include primary-related and independent operation references, unpartitioned shared
context, upstream `off`/`full`/`per_location` policies, disabled provider caching,
and later intermediate-as-root reuse. Test mutation isolation and failed fragment
publication. Use structural assertions for the explicit shared-storage requirement;
leave timing and memory comparisons to benchmarks.

Run the overview's Python checks and focused coverage:

```sh
uv run pytest -n auto tests/test_partial_metric_reuse.py tests/test_partial_operation_reuse.py tests/test_hybrid_cache_reuse.py tests/test_cache_audit_regressions.py tests/test_cache_native_ownership.py
```

Completion means fresh, fully reused, and mixed results preserve the same logical
input semantics across both execution paths. Only genuine skipped-binding rules
and any explicitly recorded edge cases remain for phase 7.

## Handoff

Record the final composition and group-selection interfaces in
[phase 7](07-availability-and-skipped-work.md) and
[phase 8](08-sql-pages-and-arrow-streams.md). Send representative shared-reference
and dense-pair workload parameters to [phase 11](11-benchmarks-and-documentation.md)
for measurement. Fix ordinary assembly defects here; do not move the core merge
algorithm to the availability or benchmark phases.
