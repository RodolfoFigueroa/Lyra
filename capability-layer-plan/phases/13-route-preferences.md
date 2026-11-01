# Phase 13 — Route preferences

Read the [shared workflow](index.md). Continue after
[phase 12](12-shortest-route-computation.md). Apply ledger decisions **5, 6, 7, 8**.

Resolve shortest-path ties with validated operation sequences declared at plugin
registration.

## Work

- Accept ordered operation references and serialize stable concrete operation
  identifiers in manifests. Infer source and target contracts, including editions,
  from each sequence.
- Validate referenced operations, canonical status, connected concrete contracts,
  sequence length, and compatibility with the shortest-route result.
- Apply a preference to its concrete source/target pair across metrics. Detect
  conflicting preferences across the complete catalog; do not introduce global
  operation priorities or per-metric route approval.
- Fail compilation for invalid or outdated preferences. A preference naming a
  route that remains the unique shortest route is still valid.
- Reject unresolved ties for any reachable compatible pair and retain the selected
  sequence in immutable route metadata.

## Acceptance and validation

- Resolve diamond and parallel-edge ties with preferences and verify the same
  choice for multiple metric consumers.
- Reject unknown or noncanonical operations, disconnected sequences, cycles that
  make a sequence longer than shortest, and conflicting preferences.
- Add a shortcut that invalidates an old preference and confirm compilation fails.
  Separately remove an alternate route and confirm the surviving preferred unique
  shortest route remains valid.
- Check edition-specific endpoint inference and registration-order independence.
- Run the index's Python checks and preference/routing tests; extend the catalog
  benchmark checkpoint where preferences materially change compilation work.

## Boundary

Do not add route overrides to public requests. Selection policy is compiled once;
execution will neither reconsider preferences nor fall back to another route.
