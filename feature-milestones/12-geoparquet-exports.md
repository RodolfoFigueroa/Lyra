# 12. GeoParquet exports

Provide one small managed GIS export interface using ordinary library behavior.
This milestone assumes retained results expose their logical schema, location
scopes, and shared references. Export those values to one portable spatial file with a small metadata
record, leaving execution reports in the associated Lyra store.

## Accepted scope

Keep GeoParquet as Lyra's managed GIS export through a thin GeoPandas/PyArrow
implementation. Require a GeoPandas version that supports Parquet attribute
persistence. Produce one file with a small Lyra metadata record embedded through
GeoPandas `attrs`, without a separate scope sidecar.

Use the native GeoDataFrame and trusted scope/result description supplied by
managed provider or result access. Prepare an independent export frame and attach
its informational metadata at this boundary. Do not infer a provider scope or
result reference from arbitrary caller-supplied frame attributes, and do not let
export preparation mutate runtime or stored values. Standalone reads and writes
use ordinary library APIs and native tables without a location-artifact wrapper.
Metadata read from a standalone file is descriptive; it does not register a provider
or establish a managed execution binding.

An exploratory in-memory write/read round trip with a supporting GeoPandas version
preserved the custom metadata, CRS, and row ID. That demonstrates feasibility, not
an exhaustive preservation guarantee; representative implementation checks remain
required. The requirement is attribute persistence support, not an invented version
number or a custom serialization framework.

For a provider-record export, include the provider ID, code revision, and declared
origin IDs/revisions, with applicable provider and origin descriptions. Include an
originating Lyra table/result reference when available. Correspondence tables
retain both source and target provider scopes. Do not assign a computed table
one input provider's record identity when the table combines multiple sources.
Metadata field spelling and reference encoding remain implementation details
consistent with shared reference contracts.

A provider scope comprises its ID, code revision, and deduplicated origin identities,
with omitted origin revisions identifying fixed unversioned data. A location ID
alone is insufficient to identify a record across provider scopes. Computed tables
instead retain their own result/table reference and applicable scopes. Metadata
must distinguish data identity from the execution that produced or reused a result.

For a location-scoped metric, obtain the owning provider scope from the retained
computation's primary-input binding. Exported scope describes that particular
result, including when it is empty or assembled from cached and fresh partitions,
and preserves its code and origin revisions even if the active registration has
changed. Keep the computed result's own reference separate from its owning
location scope.

Preserve applicable standard `location_id`, `source_location_id`, and `row_key`
columns, with active geometry named `geometry`. Geometry, CRS, and supported column
types use standard GeoParquet representation. Export reconstruction follows the
logical table schema without inventing ownership or row keys for unpartitioned
outputs.

Location-scoped metric rows use non-null string owner `location_id` and `row_key`,
unique within the owner and named output. Provider records have unique string
`location_id`. Correspondence rows identify immediate source/target pairs; their
geometry and attributes belong to the target, which can appear under several
sources. Keep both scopes without relabeling an earlier hop as the immediate
source. Other domain-named ID columns remain ordinary values.

The logical columns are strings, signed 64-bit integers, finite 64-bit floats,
booleans, and geometry, with declared nullability and CRS. Empty spatial tables
retain that schema. Library export semantics do not promise arbitrary original
dtype widths, row order, or backing-array preservation.

Execution reports remain in Lyra's retained store. An originating result reference
can lead to its execution report there, but the export does not bundle the report
or graph description and does not promise independent report access without that
store.
Shared reference parsing remains authoritative: malformed references are validation
errors, valid references with no target are not found, and broken required stored
relationships are integrity errors. Result/report access survives transient job
expiry within the supported store; the exported reference does not archive a copy
or make incompatible future stores readable.

Callers use GeoPandas `to_file` directly for Shapefile, GeoPackage, and other formats,
accepting driver conversion behavior. Remove custom driver restrictions,
format-specific conversions, routine write/read/fingerprint verification, and the
exact-preservation guarantee. Adopt library preservation semantics. Third-party
software may discard custom metadata when rewriting files; that portability limit
is part of the supported contract.

Any checksum used for a generated file concerns its bytes, not location equality
or computation identity. Equal logical tables may have different physical
arrangements and file checksums. Do not reintroduce content fingerprints, routine
readback, or identity verification as export requirements.

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

Verify native frame reads, isolation of the export copy, and metadata sourced from
trusted managed descriptions even when a caller's frame attributes have changed.
File metadata must not become an execution identity authority on readback.

Export spatial results from separate concrete block and tract metrics and from
runs across changed provider code or origin revisions, including equal bare location
IDs and empty frames. Verify that each file retains its owning scope and result
reference.

## Deferred or excluded work

No additional managed driver framework, sidecar protocol, bundled execution
report, graph archive, or exact-preservation machinery is introduced. There is no
guarantee that third-party rewrites preserve custom metadata. Export references do
not add finding-level provenance, historical-result readers, or standalone
reconstruction of external data and executable code.

## Reference points

The current [GIS exporter](../src/lyra/data/location_exports.py) is the managed
export boundary. GeoPandas documents its
[Parquet writer](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_parquet.html)
and [attribute-persistence support](https://geopandas.org/en/stable/docs/changelog.html).
