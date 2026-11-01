# Phase 25 — Bound-plan serialization

Read the [shared workflow](index.md). Continue after
[phase 24](24-public-local-execution.md). Apply ledger decisions **16, 22, 27**.

Make the immutable prepared execution plan persistable without changing its
meaning or requiring the submitting process to remain alive.

## Work

- Define a serializable representation of concrete metric/edition identity,
  effective parameters, source contracts, ordered routes, relevant pinned
  revisions, run-wide revision, and computation identity.
- Preserve the validated original request and output/source metadata needed for
  provenance and result interpretation. Keep accepted bindings separate from
  observations recorded during execution.
- Encode stable concrete operation and producer references rather than live
  Python objects, resource instances, or unqualified output names.
- Validate the stored representation and reconstruct its immutable binding data
  without choosing routes, filling defaults from a new catalog, or acquiring data.
- Reuse the shared prepared-plan model where practical. This is an internal
  persistence contract, not an additional caller-controlled request language.

## Acceptance and validation

- Round-trip native, multi-hop, multi-input, named-edition, implicit-edition, and
  unversioned plans. Preserve ordered routes, effective defaults, concrete
  identities, source/release distinctions, and explicit revision absence.
- Mutate original caller objects and current declaration defaults after creating
  a plan; reconstruction still preserves its accepted values.
- Reject malformed stored data without resource access or route computation.
- Run the index's Python checks and focused serialization/preparation tests.

## Boundary

Do not enqueue jobs, implement worker dispatch, or add legacy-envelope readers.
The next phase resolves these captured identities to worker implementations.
Existing producers need only the changes required by any shared model edits.
