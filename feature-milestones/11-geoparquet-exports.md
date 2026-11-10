# 11. GeoParquet exports

Provide one small managed GIS export interface using ordinary library behavior.
This milestone covers decision 21 and the export-related identity, schema, and
integrity constraints of decisions 3, 6, and 15 and R10 in
[the ledger](../DECISIONS.md).

## Accepted scope

Keep GeoParquet as Lyra's managed GIS export through a thin GeoPandas/PyArrow
implementation. Require a GeoPandas version that supports Parquet attribute
persistence. Produce one file with a small Lyra metadata record embedded through
GeoPandas `attrs`, without a separate scope sidecar.

For a registered dataset export, include its dataset ID, revision, and description.
Include an originating Lyra table/result reference when available. Correspondence
tables retain both source and target scope metadata. Do not assign a computed
table one input dataset's identity when the table combines multiple sources.
Metadata field spelling and reference encoding remain implementation details
consistent with shared reference contracts.

Preserve applicable standard `location_id`, `source_location_id`, and `row_key`
columns, with active geometry named `geometry`. Geometry, CRS, and supported column
types use standard GeoParquet representation. Export reconstruction follows the
logical table schema without inventing ownership or row keys for unpartitioned
outputs.

Execution reports remain in Lyra's retained store. An originating result reference
can lead to its execution report there, but the export does not bundle the report
or graph description and does not promise independent report access without that
store.

Callers use GeoPandas `to_file` directly for Shapefile, GeoPackage, and other formats,
accepting driver conversion behavior. Remove custom driver restrictions,
format-specific conversions, routine write/read/fingerprint verification, and the
exact-preservation guarantee. Adopt library preservation semantics. Third-party
software may discard custom metadata when rewriting files; that portability limit
is part of the supported contract.

## Completion boundary

Managed exports produce a single usable GeoParquet file with applicable identity,
scope, result-reference, schema, and CRS information. Existing authoring and usage
guidance describes this supported export contract and direct GeoPandas access for
other formats.

Representative round-trip checks use isolated temporary files to verify metadata,
structural IDs, geometry, CRS, and supported logical types, including empty spatial
tables and correspondence scopes. They establish supported library behavior
without routine production readback or content-fingerprint enforcement. Arrow
downloads and JSON report access remain separate functioning interfaces.

## Deferred or excluded work

No additional managed driver framework, sidecar protocol, bundled execution
report, graph archive, or exact-preservation machinery is introduced. There is no
guarantee that third-party rewrites preserve custom metadata. Export references do
not add finding-level provenance, historical-result readers, or standalone
reconstruction of external data and executable code.
