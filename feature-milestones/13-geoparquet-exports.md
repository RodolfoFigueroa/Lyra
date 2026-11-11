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
GeoPandas `attrs["lyra"]`, without a separate scope sidecar. This JSON-compatible
record has the public fields sketched below and reuses retained table descriptions.

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
Metadata field names follow the public record below. Reference strings retain
the shared contracts' opaque encoding.

A provider scope comprises its ID, code revision, and deduplicated origin identities,
with omitted origin revisions identifying fixed unversioned data. A location ID
alone is insufficient to identify a record across provider scopes. Computed tables
instead retain their own result/table reference and applicable scopes. Metadata
must distinguish data identity from the execution that produced or reused a result.

[Milestone 14](14-interventions.md) extends retained descriptions with applicable
intervention-set identities alongside base provider scopes. Export that effective
dataset context for owning, source, and target roles without changing base record
identity or losing the qualification of added records. Export the retained
effective selection, including selected additions and excluding removals, without
consulting the current base dataset or reapplying interventions. The existing
export interface and metadata record carry these descriptions.

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

Write only the declared columns, using GeoPandas `to_parquet(..., index=False)`.
Omit the pandas index even when it is named, non-default, or has several levels;
do not promote index levels into extra data columns. Structural identifiers remain
ordinary declared columns. A reader's reconstructed index is a local convenience,
not preserved identity or a row-order guarantee.

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
Compression and row-group tuning remain implementation details of the library
write, without additional public export options.

Execution reports remain in Lyra's retained store. An originating result reference
can lead to its execution report there, but the export does not bundle the report
or graph description and does not promise independent report access without that
store.
Store paths and deployment addresses remain caller-supplied context rather than
embedded metadata. A reference identifies a retained record within its associated
store or deployment; it does not locate that environment by itself.
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

### Destination files and failures

Both export methods follow milestone 11's file-download contract. Require the
destination's parent directory to exist. Write to a temporary file in that
directory, complete the write, and close the file and any retrieval response before
replacing the destination. Successful completion overwrites an existing file and
returns `None`. Use standard file operations for replacement and cleanup.

On failure or cancellation, remove temporary files and leave an existing
destination unchanged. If no destination existed, leave it absent. This applies
to retrieval, conversion, writing, and replacement failures. Local, synchronous
remote, and asynchronous remote exports share these rules; asynchronous cleanup
follows milestone 2's caller-task cancellation contract.

Expose ordinary filesystem errors, including `FileNotFoundError` for a missing
parent directory and `PermissionError` for denied access. Propagate the existing
result/input lookup exceptions unchanged, including `LocationInputUnavailableError`.
Writer failures retain their ordinary library exceptions. Reject a valid named
metric output whose declared schema is nonspatial with `ValueError`, including
when that output is empty. Do not acquire or join provider geometry implicitly.
Known empty spatial outputs and inputs remain valid exports.

For the `block_findings` result above, an existing export can be replaced:

```python
destination = Path("inspection-points.parquet")  # Already exists from an export.

with ResultStore.open(result.metadata.store_path) as store:
    written = store.export_geoparquet(
        result.metadata.result_ref, "inspection_points", destination,
    )

assert written is None
```

A failed lookup preserves that file. The byte comparison illustrates the
failure contract; it is not a production export readback requirement:

```python
from lyra.contracts.errors import ResultNotFoundError

before = destination.read_bytes()

with ResultStore.open(result.metadata.store_path) as store:
    try:
        store.export_geoparquet(
            result.metadata.result_ref, "unknown_output", destination,
        )
    except ResultNotFoundError:
        assert destination.read_bytes() == before
```

The same metric's `findings` output is a valid nonspatial table, so exporting it
as GeoParquet raises `ValueError`:

```python
with ResultStore.open(result.metadata.store_path) as store:
    try:
        store.export_geoparquet(
            result.metadata.result_ref, "findings", "findings.parquet",
        )
    except ValueError as error:
        print(error)
```

Synchronous remote callers catch the same exceptions through `client.results`;
asynchronous callers use the same methods and exceptions with `await`.

### Embedded Lyra metadata

Expose one ordinary JSON-compatible dictionary at `frame.attrs["lyra"]` after
GeoPandas readback. Both local and remote exports use the same structure:

| Field | Contract |
| --- | --- |
| `kind` | `"metric_output"` or `"location_input"`, identifying the export selection. |
| `result_ref` | The originating retained result reference. |
| `execution_id` | The execution associated with that result, including executions reusing cached values. |
| `output_name` | The selected named metric output; present only for `kind="metric_output"`. |
| `input_name` | The selected root location input; present only for `kind="location_input"`. |
| `description` | The selected output or input description serialized with `model_dump(mode="json")`. |

An output description retains `scope`, `table_schema`, and `owner_scope` from
milestone 11. A location-input description retains `kind`, `table_schema`,
`provider_scope`, `source_scope`, and `availability` from milestone 12. Its nested
`kind` distinguishes `"provider"` from `"correspondence"`; an exported input always
has `availability="available"`, including when empty. Preserve the descriptions'
null values: an unpartitioned output has `owner_scope=null`, and a direct provider
input has `source_scope=null`.

Reuse `table_schema`'s column types, nullability, descriptions, units, geometry name,
and declared CRS. Scope objects retain provider IDs, code revisions, origin IDs
and revisions, applicable descriptions, and intervention identity when present
under milestone 14's shared contract. Preserve this context through the existing
description serialization; no separate intervention sidecar is required.
The physical geometry and CRS also use standard GeoParquet metadata. The Lyra
record describes one selected table; it does
not repeat every result input/output or embed the execution report.

These sketches show the records constructed by the exporter from a freshly read,
trusted result description. Export methods continue accepting references and
names; callers do not pass these dictionaries as identity claims. For a spatial
metric output:

```python
metric_metadata = {
    "kind": "metric_output",
    "result_ref": description.result_ref,
    "execution_id": description.execution_id,
    "output_name": "inspection_points",
    "description": description.outputs["inspection_points"].model_dump(mode="json"),
}
```

For the direct provider input `blocks`, using its result's description:

```python
provider_metadata = {
    "kind": "location_input",
    "result_ref": description.result_ref,
    "execution_id": description.execution_id,
    "input_name": "blocks",
    "description": description.location_inputs["blocks"].model_dump(mode="json"),
}
```

For the operation-derived `nearby_trees` input, using its result's description:

```python
correspondence_metadata = {
    "kind": "location_input",
    "result_ref": description.result_ref,
    "execution_id": description.execution_id,
    "input_name": "nearby_trees",
    "description": description.location_inputs["nearby_trees"].model_dump(mode="json"),
}
```

The embedded correspondence description keeps both immediate provider scopes even
when source and target IDs have identical spelling. Result and execution references
remain separate from provider record identity and from any file checksum. Two
executions reusing the same values can therefore export the same data with different
result and execution references.

### Standalone readback and retained-report access

Read managed exports through ordinary GeoPandas and inspect the dictionaries:

```python
import geopandas as gpd

points = gpd.read_parquet("inspection-points.parquet")
metadata = points.attrs["lyra"]

metadata["kind"]                              # "metric_output"
metadata["output_name"]                       # "inspection_points"
metadata["description"]["scope"]              # "location"
metadata["description"]["owner_scope"]["provider_id"]  # "city_blocks"
metadata["description"]["table_schema"]["geometry"]    # "geometry"
points.crs

blocks = gpd.read_parquet("blocks.parquet")
blocks.attrs["lyra"]["description"]["kind"]          # "provider"
blocks.attrs["lyra"]["description"]["source_scope"]  # None

trees = gpd.read_parquet("block-tree-correspondences.parquet")
details = trees.attrs["lyra"]["description"]
details["kind"]                               # "correspondence"
details["source_scope"]["provider_id"]         # "city_blocks"
details["provider_scope"]["provider_id"]       # "trees"
```

Empty files retain the same record and declared schema. An unpartitioned spatial
output reads back with `scope="unpartitioned"` and `owner_scope=None`; its input
providers do not become an owning scope. Other spatial files, including files
rewritten by third-party tools, may have no `"lyra"` attribute. Ordinary library
reads return native frames regardless; file metadata neither registers providers
nor establishes managed execution bindings.

Callers supply the associated store path or deployment address separately when
following a reference. For a local export with its retained store still available:

```python
with ResultStore.open(store_path) as store:
    report = store.read_report(metadata["execution_id"])
    description = store.describe_result(metadata["result_ref"])
```

A remote caller supplies the deployment to `LyraClient` and uses
`client.reports.get(metadata["execution_id"])` or
`client.results.describe(metadata["result_ref"])`. The metadata contains no store
path or deployment URL. Lookups apply the shared reference-validation and error
contracts; reading the standalone file does not validate its references against a
store or provide an archived report. Metadata remains descriptive if a caller
edits the frame, its attributes, or the file.

### Empty tables, unpartitioned outputs, and nullable attributes

For `empty-inspection-points.parquet`, exported from a `block_findings` result
with zero inspection points, the declared columns and metadata remain available:

```python
empty = gpd.read_parquet("empty-inspection-points.parquet")
details = empty.attrs["lyra"]["description"]

assert empty.empty
assert set(empty.columns) == {"location_id", "row_key", "message", "geometry"}
assert empty.geometry.name == "geometry"
assert empty.crs.to_epsg() == 32614
assert details["scope"] == "location"
assert details["owner_scope"]["provider_id"] == "city_blocks"
assert {column["name"] for column in details["table_schema"]["columns"]} == set(
    empty.columns
)
```

Empty provider and correspondence inputs follow the same schema-preservation
rule, with their provider scopes and structural columns. Their record has
`kind="location_input"`, and its description has `availability="available"`.
No data row or index column is fabricated to carry those descriptions.

An illustrative unpartitioned spatial output declaring only `label` and
`geometry` reads back with exactly those columns:

```python
coverage = gpd.read_parquet("coverage.parquet")
details = coverage.attrs["lyra"]["description"]

assert set(coverage.columns) == {"label", "geometry"}
assert coverage.geometry.name == "geometry"
assert details["scope"] == "unpartitioned"
assert details["owner_scope"] is None
```

Location inputs used by that calculation do not add `location_id`, `row_key`,
or an owning provider to its output. Any incidental producer index is omitted;
only explicitly declared columns belong to the exported table.

For an illustrative location-scoped output with nullable string `note`, integer
`count`, float `ratio`, and boolean `reviewed` attributes, compare values by
owner and row key. This example has a populated A/main row and a B/main row whose
four attributes are missing:

```python
samples = gpd.read_parquet("nullable-points.parquet")
values = samples.set_index(["location_id", "row_key"])

assert values.at[("A", "main"), "note"] == "checked"
assert values.at[("A", "main"), "count"] == 7
assert values.at[("A", "main"), "ratio"] == 0.5
assert values.at[("A", "main"), "reviewed"]
assert values.loc[
    ("B", "main"), ["note", "count", "ratio", "reviewed"]
].isna().all()
```

`set_index()` here is a caller's analysis step after readback. The exported IDs
remain columns. Use ordinary missing-value checks rather than requiring one null
sentinel, original dtype width, or backing-array representation. Declared logical
types and nullability remain in the embedded `table_schema`; non-missing floats
retain the finite-value contract.

### Other GIS formats through GeoPandas

Use GeoPandas [`to_file()`](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_file.html)
on a retrieved native frame for other formats. For the spatial output of the
`block_findings` result, write a GeoPackage layer directly:

```python
with ResultStore.open(result.metadata.store_path) as store:
    points = store.read_table(result.metadata.result_ref, "inspection_points")

points.to_file(
    "inspection-points.gpkg",
    layer="inspection_points",
    driver="GPKG",
    index=False,
)
```

The same library method accepts frames returned by remote table or location-input
retrieval and caller-edited frames. GeoPandas and the selected driver determine
supported values, conversions, and file/layer-writing behavior. Lyra's embedded
metadata and temporary-file replacement guarantees apply to its managed GeoParquet
methods; direct `to_file()` calls carry no Lyra guarantee of custom-attribute
preservation. Use ordinary driver options for Shapefile and other supported formats.

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
Verify the `attrs["lyra"]` record and its ordinary dictionary readback for metric
outputs, direct provider inputs, and correspondence inputs. Check the selected
name, result and execution references, complete selected description, schema and
scope fields, null scopes, and empty frames. Local and remote exports use the same
record shape and leave store/deployment selection to the caller. Reused results
retain their own execution references without changing provider record identity.

Verify that named, non-default, and multi-level pandas indexes do not become
exported columns. Compare readback columns to the declared schema, including empty
tables and unpartitioned spatial outputs without structural IDs or owner scopes.
Cover populated, partially null, and all-null supported attributes, preserving
logical values and declared nullability without assertions about row order,
original dtype widths, null sentinels, or backing arrays.

Verify the local, synchronous remote, and asynchronous remote export methods,
including named-output selection, `str` and `Path` destinations, and `None` returns.
Edits to previously retrieved tables or descriptions must leave exports of the
retained originals unchanged, and remote exports must write locally for the caller.
Apply these checks to both export methods. Cover direct and correspondence inputs,
known empty inputs, and unavailable inputs raising the shared retrieval error
without upstream work. Retain the input name and result reference alongside the
appropriate provider scopes, independently of metric-output ownership.

For both export methods, verify successful replacement, preservation of existing
destination bytes on failure or cancellation, absence of a previously nonexistent
destination after failure, and temporary-file cleanup. Cover lookup, conversion,
writer, and replacement failures, missing parents, and denied access using isolated
temporary files and mocked failure boundaries. Verify the same contract for local,
synchronous remote, and asynchronous remote calls. A valid nonspatial output,
including an empty one, raises `ValueError` without acquiring geometry or changing
the destination; valid empty spatial tables still export. Keep shared lookup errors
and ordinary filesystem/library exceptions intact.

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
