# Phase 12 — Shortest-route computation

Read the [shared workflow](index.md). Continue after
[phase 11](11-canonical-operation-validation.md). Apply ledger decisions **5, 7, 8**.

Precompute shortest canonical routes over concrete contracts in the complete
enabled catalog.

## Work

- Treat each canonical operation as one directed edge with equal positive cost.
  Find reachable contract pairs and their minimum operation counts.
- Use a zero-operation route for an identical concrete source and target contract.
  Matching geography or structural schema alone is insufficient.
- Detect multiple shortest operation sequences, including parallel operations
  between the same contracts. Do not resolve ambiguity using registration order.
- Support directed cycles and separately declared reverse conversions without
  enumerating all possible paths or treating edges as inverses.
- Store deterministic unique routes and useful ambiguity information in compiled
  metadata. Until preferences are available, unresolved ties are catalog errors.
- Preserve exact selected operation identities for later binding. The algorithm
  makes no claim that alternate routes yield equivalent selections.

## Acceptance and validation

- Cover direct, multi-hop, unreachable, shortcut, parallel-edge, diamond, and cyclic
  graphs. A same-contract request always uses its zero-hop route.
- Reordering plugin or operation registration does not change a selected route or
  hide an ambiguity.
- Exercise a graph with many potential paths to protect the bounded algorithmic
  approach without timing-based assertions.
- Run the index's Python checks and routing/catalog tests. Measure representative
  compilation sizes and graph shapes as an initial routing checkpoint.

## Boundary

Do not add weights, runtime route search, fallback routing, SQL planning, or
equivalence checking. Explicit preferences are the next phase.
