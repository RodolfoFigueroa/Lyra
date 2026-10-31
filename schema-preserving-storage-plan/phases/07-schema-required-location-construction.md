# Phase 07 — Schema-required location construction

After phase 06, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Change the construction boundary and its
callers before replacing the location representation.

## Work

- Require a registered or explicitly supplied Pandera location schema whenever a
  producer frame becomes a runtime Locations value. Reuse the shared normalized
  validation path and retain the declaration-derived information storage needs.
- Ensure provider resolution, location operations, direct SDK construction, and
  internal frame reconstruction use the appropriate contract. Avoid per-value
  schema inference and public flags that bypass validation.
- Remove generic dictionary/JSON runtime constructors. Keep job/request payload
  parsing and explicit presentation exports where they serve their existing
  purposes; removing a runtime constructor does not remove all JSON handling.
- Update affected examples and tests to construct declared native frames. Use
  concise fixture builders with explicit known schemas so repetitive fixture
  changes do not obscure assertions. Keep invalid-input tests focused on the
  intended failure boundary.
- Keep artifact restoration distinct: its authority is the verified stored type
  contract, followed by current producer validation when used in execution.
  Do not turn the new producer-schema requirement into a plugin dependency for
  standalone artifact readers.

## Acceptance

- Producer construction without an appropriate schema fails. A valid schema and
  frame succeed through direct, provider, and operation paths.
- Exact columns, nullability, declared coercion, ID uniqueness, geometry role,
  and CRS are enforced without mutating the supplied frame.
- Required-column and invalid-value tests remain meaningful after fixture
  conversion. Tests no longer rely on unrestricted sparse dictionaries as valid
  runtime values.
- Existing public callers and exports remain functional. No compatibility alias,
  silent schema inference, or unvalidated construction escape hatch is added.

## Verification and boundary

Follow shared verification, cover construction and immediate callers, and run
the full suite because fixture and public API changes are broad. This phase owns
the schema requirement and caller updates. The typed property representation and
complete native dtype fidelity are connected in phase 08; do not expand this phase
into cache or storage redesign.
