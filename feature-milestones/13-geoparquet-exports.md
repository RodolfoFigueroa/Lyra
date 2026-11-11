# 13. GeoParquet exports

Provide one small managed GIS export interface using ordinary library behavior.
This milestone builds on retained named outputs and
[retained location inputs](12-retained-location-inputs.md), including their logical
schemas, location scopes, and shared references. Export those values to one portable
spatial file with a small metadata record, leaving execution reports in the
associated Lyra store.

## Accepted scope

Keep GeoParquet as Lyra's managed GIS export through a thin GeoPandas/PyArrow
implementation. Require a GeoPandas version that supports Parquet attribute
persistence. Produce one file with a small Lyra metadata record embedded through
GeoPandas `attrs`, without a separate scope sidecar.

Use the native GeoDataFrame and trusted description supplied by retained result
or location-input access. Prepare an independent export frame and attach
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

For a direct provider-input export, include the provider ID, code revision, and declared
origin IDs/revisions, with applicable provider and origin descriptions. Include an
originating result reference and the selected input name. Correspondence tables
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

## API sketches

### Managed result exports

Expose `export_geoparquet(result_ref, output_name, path)` on `ResultStore` and
`client.results`. The arguments and return value are the same locally and remotely:

| Element | Contract |
| --- | --- |
| `result_ref` | A string identifying a retained result in the selected store or deployment. |
| `output_name` | The name of one spatial output, including an empty spatial output. |
| `path` | A local destination filename, accepting `str` or `pathlib.Path`. |
| Return value | `None`, matching milestone 11's file-writing client methods. |
| File format | Always GeoParquet, with one named output per file. |

For a local `result` from milestone 6's `block_findings` metric:

```python
from pathlib import Path

from lyra.runtime.results import ResultStore

with ResultStore.open(result.metadata.store_path) as store:
    store.export_geoparquet(
        result.metadata.result_ref,
        "inspection_points",
        Path("inspection-points.parquet"),
    )
```

The store remains read-only; the export writes the destination file. Retrieval
uses the retained result reference and output name without running handlers or
acquiring provider data.

For a result retained by a remote deployment:

```python
from lyra.api import LyraClient

with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    client.results.export_geoparquet(
        result_ref,
        "inspection_points",
        "inspection-points.parquet",
    )
```

The remote method writes to the caller's local filesystem. It can reuse milestone
11's result-description and Arrow retrieval interfaces; this export interface
requires no additional HTTP endpoint.

`AsyncLyraClient` exposes the same method, arguments, and return value with `await`:

```python
from lyra.api import AsyncLyraClient


async def export_points(result_ref: str) -> None:
    async with AsyncLyraClient(
        "lyra.example.com", agent_api_key="agent-key"
    ) as client:
        await client.results.export_geoparquet(
            result_ref,
            "inspection_points",
            "inspection-points.parquet",
        )
```

### Retained values and export sources

The exporter retrieves the selected table and its matching retained description,
then prepares an independent export frame with the informational metadata. Schema
and owning scope come from that result, including after provider registrations
change. This performs another retained read even when the caller already has the
table in memory.

Editing `result.tables["inspection_points"]` or its accompanying metadata does
not change what this method exports: it exports the retained original. The method
accepts no caller-supplied frame or description. Callers saving their own edited
frames use ordinary GeoPandas writing.

These entry points address the root metric's retained named spatial outputs.
Provider records and correspondence tables are exported through the root's named
location inputs, using milestone 12's retained values and descriptions.

### Location-input exports

Expose `export_location_input_geoparquet(result_ref, input_name, path)` on
`ResultStore` and `client.results`. It accepts a `str` or `Path` destination and
returns `None`, with the same local-file behavior as named-output export.
For the local `block_findings` result above:

```python
with ResultStore.open(result.metadata.store_path) as store:
    store.export_location_input_geoparquet(
        result.metadata.result_ref,
        "blocks",
        "blocks.parquet",
    )
```

For a remote result with milestone 12's illustrative operation-derived
`nearby_trees` input:

```python
with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    client.results.export_location_input_geoparquet(
        result_ref,
        "nearby_trees",
        "block-tree-correspondences.parquet",
    )
```

The async client exposes the same method with `await`. Remote exports use the
location-input description and Arrow access from milestone 12 and write locally
for the caller, without an additional export endpoint.

A direct input preserves unique provider records. A correspondence input preserves
immediate source/target pairs, both scopes, and target geometry and attributes,
including repeated targets under different sources. Use the originating
`(result_ref, input_name)` to identify this input selection separately from provider
record identity. Known empty inputs export their declared schema, scopes, and CRS.
Inputs with `availability="not_materialized"` raise `LocationInputUnavailableError`;
exports perform no provider acquisition or operation execution. All input lookup
errors follow milestone 12, including integrity failures for missing required data.

Previously returned frames and descriptions cannot change these exports. They
read retained inputs, including the cached and fresh portions of a result, without
adding ownership columns or metric row keys to provider or correspondence tables.

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

Verify the local, synchronous remote, and asynchronous remote export methods,
including named-output selection, `str` and `Path` destinations, and `None` returns.
Edits to previously retrieved tables or descriptions must leave exports of the
retained originals unchanged, and remote exports must write locally for the caller.
Apply these checks to both export methods. Cover direct and correspondence inputs,
known empty inputs, and unavailable inputs raising the shared retrieval error
without upstream work. Retain the input name and result reference alongside the
appropriate provider scopes, independently of metric-output ownership.

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
Location-input exports cover the selections retained for a completed result;
fresh provider browsing and arbitrary internal graph-node exports remain outside
this interface.

## Reference points

The current [GIS exporter](../src/lyra/data/location_exports.py) is the managed
export boundary. GeoPandas documents its
[Parquet writer](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_parquet.html)
and [attribute-persistence support](https://geopandas.org/en/stable/docs/changelog.html).
