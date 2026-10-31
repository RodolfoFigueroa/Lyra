# Phase 09 — Presentation exports and inspection

After phase 08, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Complete explicit presentation behavior
for the newly typed runtime.

## Work

- Review feature iteration, dictionary/JSON/GeoJSON exports, previews, and public
  result serialization against typed scalar values. Use standard serialization
  behavior, including native handling of nulls and temporal values; do not invent
  lossless JSON tags or promise restoration of pandas dtypes from presentation
  output.
- Preserve bounded feature batches, preview limits, and detached mutable exports.
  Metadata inspection must not materialize the full dataset. Decode only the
  rows and columns needed by the requested view.
- Update standalone artifact inspection to report the stored typed structure and
  spatial metadata without loading a plugin or executing producer checks. Full
  artifact integrity verification may read buffers; it must not require building
  all feature objects.
- Review existing explicit GIS file exports using declared types rather than
  row-based inference. Preserve their supported value/geometry guarantees and
  fail clearly for unsupported contents instead of silently narrowing values.
  Keep optional driver behavior isolated from the core storage implementation.
- Update relevant public API descriptions and examples to distinguish typed
  frame/artifact contracts from presentation serialization.

## Acceptance

- Bounded exports do not decode rows beyond the limit. Property-only access and
  metadata inspection avoid geometry decoding.
- Nullable, temporal, empty, and numeric-boundary cases either serialize through
  supported standard behavior or receive a clear format-specific error. No
  custom lossless encoding or silent precision change is introduced.
- Exported objects can be mutated without affecting stored values or identity.
  Spatial metadata and feature order remain correct.
- Existing GIS export success, unsupported-content, and destination-protection
  behavior remain covered. Explicit GIS exports are not additional cache backends.

## Verification and boundary

Follow shared verification and run focused export, artifact-inspection, and
public-result tests. Use temporary files and mocked optional-driver boundaries
where needed. Do not add runtime import constructors or broaden the set of export
formats as part of this phase.
