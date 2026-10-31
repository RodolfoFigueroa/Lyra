# Phase 02 — Schema and frame normalization

After phase 01, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Establish consistent producer validation
before changing storage.

## Work

- Reject explicit Pandera index declarations during registration, for both
  DataFrameSchema and DataFrameModel declarations. Retain ordinary column and
  compound-key uniqueness checks.
- Normalize every produced metric and location frame to a fresh RangeIndex
  before Pandera validation. Preserve row order, active geometry, and CRS; do not
  mutate the producer's frame.
- Require exactly the declared columns, including on empty results. Reject
  missing, extra, and duplicate columns before normalization can hide them.
  Reorder valid columns to declaration order before checks run, including when
  the schema declares ordered columns.
- Use the same normalization rules for registered fresh and restored-result
  validation. Keep native DataFrame/GeoDataFrame returns and named metric outputs.
- Remove index-specific validation and author-facing promises that contradict
  the new policy. Update affected metadata, examples, and tests as needed.

## Acceptance

- Filtered, sorted, named, duplicate, and multi-level producer indexes become
  RangeIndexes without changing rows or producer-owned data.
- Custom checks observe the normalized index and declared column order. A
  shuffled valid producer frame succeeds; genuinely incorrect columns fail.
- Empty frames must declare all columns. Geometry selection and CRS survive
  normalization, including non-default structural column names.
- Column uniqueness, including compound keys, still fails for invalid data.
  Explicit index declarations fail at registration with useful errors.
- Fresh and restored-result validators apply the same rules. No index values or
  names are promoted into output columns.

## Verification and boundary

Follow shared verification; test registration, native results, location producer
validation, and execution callers, then run the full suite. Leave typed storage
conversion to phase 03. Removing obsolete cache index-restoration machinery can
wait for the cache replacement, provided current execution returns normalized
frames.
