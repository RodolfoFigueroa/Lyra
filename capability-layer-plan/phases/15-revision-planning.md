# Phase 15 — Revision planning

Read the [shared workflow](index.md). Continue after
[phase 14](14-accepted-source-selections.md). Apply ledger decisions **18–23**.

Compile dependency-aware location revision identities separately from the
catalog-wide structural computation namespace.

## Work

- Derive the structural namespace from graph contracts, provider contracts,
  installed package versions, and existing computation identity. Exclude location
  revision values wherever they appear in serialized declarations.
- Compute each node's relevant revision closure from native location contracts and
  transitive declared calculation dependencies. Include a location-producing
  operation's target dataset revision when declared.
- Scope revision identities to concrete location contracts. Preserve omitted
  revisions as unversioned; metrics and operations declare no independent revisions.
- Distinguish revisions needed for a selected public route from revisions needed
  by the metric's native inputs and calculation graph. Selection-only revisions
  stop at the native-input boundary; a contract also used by calculation remains
  relevant there. Do not collect revisions from every possible route.
- Provide immutable revision bindings for preparation to pin. Keep the optional
  run-wide cache revision as an additional run namespace component that never
  replaces declared location revisions.

## Acceptance and validation

- Test native, transitive, target, shared, derived-unversioned, and unrelated
  location dependencies. Equal revision strings on different contracts remain
  distinct identities.
- A revision-only change preserves the structural namespace and unaffected
  dependency identities; a structural contract or package-version change may
  change the namespace.
- Verify the selection/calculation boundary with the same location contract used
  in both roles, and with alternate routes that are not selected.
- Run the index's Python checks and identity/compilation tests.

## Boundary

This phase computes identities and bindings. Execution-time enforcement and cache
integration follow in [pinned acquisition](17-pinned-location-acquisition.md) and
[revision-aware caching](20-revision-aware-invocation-caching.md). Do not add code
hashing, data snapshots, independent node versions, or a general external-source
revision system.
