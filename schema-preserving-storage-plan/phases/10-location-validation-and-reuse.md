# Phase 10 — Location validation and reuse

After phase 09, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Keep executable validation complete while
eliminating redundant storage work.

## Work

- Validate fresh and cache-restored location results against the current
  registered Pandera contract before downstream use. Stored type metadata or a
  previously validated marker cannot replace executable checks.
- Reconstruct typed validation frames through the shared conversion path.
  Remove scalar repair and dtype inference left over from sparse JSON storage.
- When validation leaves contents and type contracts unchanged, reuse the owned
  snapshot and its cached fingerprints. Avoid re-ingesting geometry or rebuilding
  all buffers merely to establish that the existing value remains valid.
- If declared coercion changes contents or types, produce the required new owned
  storage and identity while reusing unchanged safe components where practical.
  Preserve isolation even if validation operates on mutable frame copies.
- Keep subset creation separate from operation-result validation. Reuse within
  the existing completed-invocation state must not become a general cache of
  arbitrary validation results across changed contracts or executions.

## Acceptance

- A stored artifact can pass structural checks yet fail the current producer's
  custom check. That failure prevents downstream consumption.
- Custom checks really execute for fresh and restored validation; metadata and
  fingerprint equality do not skip them.
- Instrumented tests show no geometry re-ingestion or snapshot rebuild for
  unchanged revalidation. Ordinary repeated access does not recompute identity.
- Coercion that legitimately changes data yields correct new values and
  fingerprints without modifying the source snapshot.
- Slices do not rerun whole-table checks; an operation returning a slice is
  validated against its own declaration. Validation errors retain useful
  operation/input/output context.

## Verification and boundary

Follow shared verification, test validation, provider/operation boundaries, and
cache-restored validation, then run the full suite. Prefer behavioral counters
at expensive boundaries over timing assertions. Do not suppress Pandera checks,
introduce consumer input schemas, or redesign executor scheduling.
