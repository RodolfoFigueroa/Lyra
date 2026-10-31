# Phase 12 — Location artifact and cache integration

After phase 11, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Complete integration coverage for the
location artifact routing introduced during phase 08.

## Work

- Trace location construction, standalone artifact reads, persistent cache hits,
  and downstream consumption through the shared typed codec. Remove remaining
  duplicated storage logic or obsolete representation assumptions.
- Confirm location bundles use uncompressed IPC and preserve the stored type
  contract, scope, IDs, geometry, CRS, and content identity. Keep atomic bundle
  and cache-miss behavior consistent with metric persistence.
- Verify integrity and ownership at standalone reads without requiring plugin
  installation. At execution reuse, additionally run the current operation schema
  through the validation path from phase 10.
- Exercise cache failure handling for malformed metadata, corrupted contents,
  fingerprint mismatches, partial publication, and unsupported versions.
- Cover cache-restored selections, subsequent frame exports, and mixed graphs so
  typed properties cannot be lost after the initial artifact decode.

## Acceptance

- Fresh, standalone-restored, and cache-restored locations have identical typed
  contents, spatial metadata, and fingerprints for supported fixtures.
- Empty/all-null data, temporal precision, numeric boundaries, signed zero, and
  custom structural column names survive the full persistence path.
- Standalone readers reject invalid artifacts clearly. Cache execution treats
  unusable entries as misses and recomputes without passing invalid data onward.
- A valid cache hit avoids producer execution and geometry re-ingestion while
  still running current Pandera checks. Mutating exported frames cannot poison
  stored values or later cache results.
- The representation and content versions are enforced directly. No compressed
  location-storage branch or legacy decoder remains in the active implementation.

## Verification and boundary

Follow shared verification, run artifact-reader, location-cache, validation-reuse,
and mixed-graph tests, then run the full suite. Reuse existing test coverage when
it already proves a requirement. Do not rewrite the codec or add alternate
storage protocols merely to complete this integration audit.
