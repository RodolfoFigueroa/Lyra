# Schema-preserving storage decisions

Lyra handles DataFrames and GeoDataFrames with strict, predefined Pandera output
schemas. Storage and export must preserve that contract. Closed-alpha development
permits breaking changes: no migrations, shims, compatibility aliases, or legacy
artifact readers are required.

## Accepted

| Decision | Rationale |
| --- | --- |
| Keep native producer frames, named metric outputs, and native frames between metric dependencies. Share column conversion rules across metrics and locations. | Preserve familiar authoring and avoid unnecessary pandas–Arrow round trips. |
| Use Pandera declarations/validation and Arrow's native types, conversions, and pandas metadata, with minimal integration code. | Do not duplicate library functionality with a Lyra dtype system or large handwritten mapping. |
| Establish storage types from the declaration independently of rows; retain them through selection and artifacts. Export declared frame dtypes. | Empty/all-null results must remain typed. Consumers should not recover types lost through generic tagged properties or blanket `object` exports. |
| Use standard categorical and temporal declarations, including timestamp precision/timezone and Arrow-backed date-only types where needed. Keep categorical handling limited to native library support and straightforward integration. | Categories are not expected in the near term. Their edge cases should not drive custom encodings, repair machinery, or exhaustive verification in the first implementation. |
| Require exactly the declared columns, including empty results. Normalize missing markers to one logical null, allowed only by schema nullability. Remove per-row property absence and `MISSING`. | The contract describes complete typed columns, not sparse property dictionaries or unrestricted Python objects. |
| Normalize produced columns to declaration order without mutating the producer's frame. Explicit projections preserve requested column order. | Equivalent outputs receive consistent storage and identity without requiring authors to arrange columns themselves. |
| Reject infinity and out-of-range values, preserve signed zero, and apply only declared coercion. | Storage must not introduce implicit changes in scalar meaning or precision. |
| Construct runtime values using a registered or explicitly supplied schema; artifact reads use the stored type contract. | Construction must not bypass the agreed schema requirements. |
| Use uncompressed Arrow IPC as the single unified artifact format for metrics and locations, preserving the existing geometry contract. | Prioritize execution speed and low processing overhead over smaller files. Observed file sizes are acceptable, and uncompressed IPC generally delivered the fastest measured writes and reads. Integrated performance still needs verification. |
| Keep feature/JSON/GeoJSON exports explicit, using standard serialization behavior. | Arrow artifacts carry the typed storage contract; presentation exports do not promise pandas dtype preservation. |

Use one straightforward storage implementation, without compression settings,
configurable backends, or automatic format selection. The
[storage benchmarks](storage_benchmark_report.md) document the measured tradeoffs;
the choice prioritizes processing overhead rather than winning every read scenario.

## Index and row identity policy

Normalize every produced metric and location frame to a fresh `RangeIndex` before
output validation, preserving row order and columns without mutating the
producer's frame. Ignore index values, names, and dtypes in storage, fingerprints,
and cache reconstruction. Reject explicit index declarations at registration.

Authors may use indexes internally. Important identifiers and grouping keys belong
in declared columns, with ordinary Pandera uniqueness constraints, including
compound keys. Consumers align by those columns. This avoids failures caused by
incidental indexes after filtering or sorting.

## Validation and caching

- Validate fresh and cache-restored results against the current registered Pandera
  contract before downstream use. This retains existing behavior and gives both
  paths the same guarantees.
- Standalone readers verify stored structure, types, and integrity; arbitrary
  producer checks require the registered plugin. Metadata cannot replace executable
  validation code.
- Reuse validated results. Avoid rebuilding unchanged snapshots or re-ingesting
  geometry for validation. Full Pandera checks still incur their actual cost.
- `slice()` and `select_ids()` preserve the stored type contract without rerunning
  arbitrary producer checks. Whole-table requirements, such as a minimum row count,
  need not hold for a useful subset. An operation returning that subset must satisfy
  its own declared output schema.

## Content and computation identity

- Keep SHA-256 content fingerprints over normalized typed contents: column names,
  order, types and relevant parameters, values, nulls, and row order. Location
  identity also includes IDs, geometry, collection, unit type, and CRS. Exclude
  pandas indexes, descriptive prose, and validation functions from data identity.
- Use exact identity. Changes to scalar types, row order, or geometry coordinates
  affect fingerprints. Identical typed contents retain identity across artifact
  round trips, equivalent slices, and Arrow chunk boundaries.
- Cache fingerprints only on owned immutable snapshots and verify them on artifact
  reads. Avoid repeated hashing of mutable metric frames during ordinary execution.
- Keep the existing computation cache key: node identity, effective parameters,
  input location fingerprints, registered graph contracts, plugin versions, and
  `cache_revision`. Conservative graph-wide invalidation is sufficient. External
  source changes and unversioned implementation changes require a revision update;
  result validation cannot detect source freshness.
- Version changed artifact/content protocols and support the new representation
  directly, without compatibility machinery.

## Deferred or excluded from the first implementation

| Item | Reason |
| --- | --- |
| Consumer input schemas | Producers and consumers are maintained together. Avoid duplicated declarations; use contextual execution errors and integration tests. Producer validation alone does not prove every consumer assumption. |
| Generic dictionary/JSON constructors and custom lossless JSON encoding | No demonstrated need; validated frames and typed artifacts suffice. |
| General-purpose type frameworks, semantic-equivalence hashing, source-code inspection, and fine-grained cache invalidation | Library support, exact identity, and existing conservative keys meet the narrow requirements. |
| Executor concurrency, geometry-format redesign, and a new public metric-result wrapper | Independent changes unnecessary for schema preservation. |
| Custom categorical codecs and exhaustive categorical edge-case work | No near-term use is expected. Known native empty-category round-trip limitations should remain explicit, without blocking the first implementation on perfect categorical fidelity. |

## Acceptance criteria

Verify typed fidelity across fresh results, selections, and cache reads, including
empty/all-null data, temporal precision, numeric boundaries, spatial
metadata, normalized indexes, and mutation isolation. Preserve lazy inspection,
selective decoding, bounded exports, and single-pass geometry ingestion. Test
fingerprint sensitivity and layout independence. Give categorical handling basic
native round-trip coverage; retain known limitations without custom repair logic.
Verify the integrated IPC cache path, including ownership, fingerprints, and
validation. Existing format benchmarks do not establish end-to-end performance;
comparisons must use equivalent valid typed fixtures and distinguish validation,
conversion, and storage costs.
