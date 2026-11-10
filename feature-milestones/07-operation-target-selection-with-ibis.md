# 7. Operation target selection with Ibis

Let operations select target candidates from a registered provider without acquiring
external resources themselves. Extend the shared executor from
[milestone 6](06-unified-graph-execution.md) with lazy Ibis filters and managed
provider adapters, then supply validated candidate records to ordinary mapping
handlers. Storage, caching, and reporting build on this execution boundary later.

## Accepted scope

### Computational operations and optional selectors

Operations compute deterministically from declared inputs and effective parameters.
Their mapping handlers and target selectors perform no direct external I/O: no
database queries, file reads, network requests, or resource acquisition. Reject
operation resource bindings and direct external-origin declarations during
registration or plan validation. Providers and managed adapters own location
acquisition; metrics retain their declared external-acquisition contract.
Trust authors to obey the computational restriction without inspecting Python
handler code, intercepting arbitrary I/O, or introducing an execution sandbox.

An operation may declare one optional target-selector callback for its existing
target provider. It receives resolved source GeoDataFrames, applicable declared
native dependency tables and effective parameters, and an unbound Ibis table
representing that provider's registered schema. Named bindings remain the authority
for those dependencies; the callback introduces no hidden inputs. It returns a lazy
Ibis table expression selecting target records. The table is a symbolic relation,
not an in-memory copy of the provider's collection.

The operation's source inputs and target declare concrete provider IDs under
milestone 6. Resolve target acquisition through its provider registration and
declared origins. Source and target providers may differ. Changing a source
selection does not change the target provider or its scope. Callers cannot bind
another target or make a selector choose one at execution time.

Lyra executes the expression through the target provider's managed adapter and
supplies the validated result as a separate GeoDataFrame candidate input to the
mapping handler. Candidates do not participate in primary-input inference: the
operation's source inputs still determine its primary input and source partitions.
The selector cannot depend on its own candidates or mapping result. Authors do
not call `.execute()`, `.to_pandas()`, or another materialization method on the
symbolic expression, and receive no backend connection through it.

Apply milestone 6's native-table ownership boundary to both callbacks. The selector
and mapping handler receive independent working frames; editing the selector's
inputs cannot alter the mapping handler's inputs or the runtime's original
selection. Candidate edits inside the mapping handler cannot change the
authoritative target records used for forwarding. Provider scopes, including code
revisions and origin identities, stay in runtime bindings for each source/target
role, independent of frame attributes.

Operations that derive target IDs from their supplied inputs need no selector or
query-capable adapter. Their declared target provider resolves returned IDs through
the ordinary exact-ID path. Candidate selection is optional context for mapping;
it does not create or register locations or replace the operation's output contract.

### Single-provider filters

Support filters over the one supplied target relation, preserving every registered
column, its value, and the unique `location_id` of each matched record. Predicates
may combine target attributes, constants, and scalar or geometry literals computed
from declared in-memory inputs. Use ordinary deterministic row-wise Ibis predicates,
including comparisons, Boolean composition, null tests, literal membership, and
spatial predicates supported by the selected backend. Attribute comparisons combined
with geometry intersection and distance-within predicates must work on both adapters.
Apply Ibis/backend null semantics: only a true filter predicate selects a row.

Validate the expression's relation and shape before execution. Reject additional
relations, joins, subqueries, aggregates, windows, projections, computed output
columns, unions, sorting, limits, offsets, sampling, raw SQL, and user-defined
functions. Chained filters on the supplied relation are supported. These are
structural restrictions on ordinary Ibis expressions, not a custom query language
or a replacement expression engine. Unsupported expressions and backend operations
fail clearly instead of falling back to Python filtering or another data source.

The selector computes candidate membership; the mapping handler computes each
source's actual correspondences. A selector may acquire a superset for the handler
to refine. Authors are responsible for acquiring enough candidates for their
intended mapping and for results being independent of input row order.

### Managed provider adapters

Provide standard adapters for Parquet/GeoParquet through Ibis's DuckDB backend with
spatial support, and PostGIS through Ibis's PostgreSQL backend. Authors configure
the registered provider's file source or database relation and ordinary resource
bindings; they do not implement a query adapter or construct SQL. The adapter
supports predicate selection and exact-ID resolution against the same provider
scope and executable schema. The provider declares every origin used by the
adapter; resource bindings provide only I/O configuration and lifecycle.

Registration and discovery expose the declared adapter capability and target schema
without opening files, obtaining credentials, connecting to services, or inspecting
external tables. A selector targeting a provider without a query-capable adapter
fails plan validation. Connections and backend setup follow the existing runtime
resource lifecycle, only when acquisition is needed. API/worker transport continues
to contain normalized requests and effective parameters, not Ibis expression
objects, backend handles, or captured executable callbacks.

Expose canonical registered columns and logical types to Ibis. Decode geometry
using standard library support: GeoParquet metadata, WKB geometry in plain Parquet
with CRS supplied by the registered schema, or PostGIS geometry. Incompatible
geometry encodings, types, and conflicting CRS metadata fail acquisition. Preserve
all schema-defined attributes and validate records once at the ordinary producer
boundary, including strict string IDs, uniqueness, nullability, geometry, and CRS.
The registered schema also supplies the shape of empty results.

Spatial comparisons require compatible geometry CRS and distance units. Do not
implicitly interpret geographic degrees as metres or silently reproject target
records. Authors can transform source geometry in memory using existing libraries
before constructing literals in the target CRS. Validate detectable CRS mismatches;
authors remain responsible for meaningful units and spatial predicates.

Bind the expression to the registered source and execute filtering in the backend
before materializing candidate records into Python. Acquire the complete matching
set without implicit preview limits, pagination loss, or truncation. This does not
guarantee index use, avoidance of complete file scans, or a small matching set.
Choose and verify a compatible Ibis/backend dependency combination during
implementation; support for these adapters is not a promise that every Ibis
operation or library version works identically on both.

### Execution, forwarding, and identity

Use the same stages in local, development, and API/worker execution:

1. Resolve the operation's required source and dependency inputs.
2. Construct and validate the lazy target filter without external acquisition.
3. Execute it at the managed provider boundary and validate its candidate records.
4. Invoke the mapping handler with its ordinary inputs and candidates.
5. Validate the returned mapping and forward source/target correspondence rows.

Operations still return `dict[str, tuple[str, ...]]`. Preserve supplied-source
membership checks, unique targets within each source group, shared targets across
groups, and completed empty groups. Check source membership against the original
runtime selection. Reuse the runtime's validated candidate records when
forwarding selected targets; resolve any returned IDs not already available through
the ordinary exact-ID provider path. Missing requested targets remain errors. Do
not reacquire candidate records merely to assemble correspondence rows.

Zero-source operation requests require no selector invocation, target acquisition,
or fabricated mapping invocation. A successful selection with no matches supplies
schema-valid empty candidates to the handler for a nonempty source request. Query,
connection, decoding, validation, and mapping failures remain failures; none are
converted into a successful empty selection or partial mapping.

A provider scope contains its ID, code revision, and deduplicated origin identities.
It fixes searchable membership and every record's contents, including fixed
unversioned origins. Changed external contents require an updated origin identity;
output-affecting acquisition or transformation changes require an updated provider
code revision or ID. Adapter configuration must honor that scope. A revision label
does not archive a historical source snapshot.

The selector is part of the operation's implementation. Its changes follow the
same author-managed `(node_id, code_revision)` rule as mapping changes. Declared
source/dependency identities, effective parameters, target provider scope, and
implementation identity determine selection and mapping together. Candidate IDs
and constructed expressions need not be known to form the operation's computation
identity. Do not add expression hashes, independent selector revisions, query
cache keys, or adapter credentials to it. Provider record identity remains exactly
`(provider_id, code_revision, origin identities, location_id)` regardless of
acquisition path. Provider revisions cover output-affecting adapter changes;
operation revisions cover selection and mapping changes.

Full and uncached execution select candidates for the operation's complete bound
inputs. Per-location execution may select for the needed source partitions while
retaining complete shared inputs. Its author-owned independence promise covers
selection and mapping together: another source entering or leaving a physical batch
must not change a partition's result. A shared candidate pool may grow with the
batch, provided each source's mapping remains independent of that growth.

Later caching can bypass selection and acquisition on a valid operation or
downstream hit. Individual cached records do not establish complete query
membership, including absence of matches. When selection is needed, execute its
query even if some matching records are already retained; returned records can
satisfy subsequent exact-ID needs. Preserve acquisition outcomes at this boundary
for later reporting, separately from selector construction and mapping execution.

### Authoring example

Assume the registered `city_blocks` provider exposes `location_id` and `geometry`,
and the `trees` provider also exposes `height`. Both schemas use projected
EPSG:32614, with coordinates and tree heights in metres and non-null valid geometries.
Trees are points. This illustrative decorator spelling describes the proposed interface;
the Ibis and GeoPandas calls use their ordinary library APIs.

```python
import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from lyra.sdk import location_operation


def select_mature_trees(blocks: gpd.GeoDataFrame, trees: ir.Table) -> ir.Table:
    if blocks.empty:
        return trees.filter(ibis.literal(False))
    area = ibis.literal(
        blocks.geometry.union_all(), type=dt.GeoSpatial(srid=32614)
    )
    return trees.filter(
        trees.height > 5,
        trees.geometry.d_within(area, distance=100),
    )


@location_operation(
    name="mature_trees_near_blocks",
    locations={"blocks": "city_blocks"},
    target="trees",
    target_selector=select_mature_trees,
)
def mature_trees_near_blocks(
    blocks: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame
) -> dict[str, tuple[str, ...]]:
    matches = gpd.sjoin(
        blocks[["location_id", "geometry"]].rename(
            columns={"location_id": "block_id"}
        ),
        candidates[["location_id", "geometry"]].rename(
            columns={"location_id": "tree_id"}
        ),
        how="inner",
        predicate="dwithin",
        distance=100,
    )
    groups = matches.groupby("block_id")["tree_id"].agg(tuple).to_dict()
    return {block_id: groups.get(block_id, ()) for block_id in blocks.location_id}
```

The union selects trees near any supplied block; the in-memory spatial join assigns
them to individual blocks. Height must exceed five metres, while a distance of
exactly 100 metres qualifies. Trees inside a block have zero distance. One tree
can appear under several blocks, and a block without matches returns an empty
tuple. The in-memory join is ordinary computation on resolved inputs; it is not
an additional relation in the backend filter.

## Completion boundary

Selector-backed and ID-only operations work through the common local and worker
runtime. Both adapters apply attribute and spatial filters before Python candidate
materialization, preserve registered schemas and revisions, and use ordinary
mapping validation and forwarding. Discovery and planning remain service-free.
Resolved sources and candidates are native GeoDataFrames; only the target query
relation remains a lazy Ibis table.
Examples and authoring guidance explain computational operations, configured
provider adapters, and ordinary Ibis filters without a second query DSL.

Isolated behavioral checks cover combined attribute/spatial predicates, inside and
boundary-distance matches, height thresholds, shared targets, empty sources and
candidates, unordered inputs, invalid mappings, schema/CRS failures, unavailable
adapter capabilities, unsupported expressions, and backend errors. Exercise both
local and worker paths, validated-candidate reuse, and ordinary ID-only resolution.
Verify that selector mutations do not reach mapping inputs, mapping mutations do
not alter forwarded records, and ownership checks use the original selections.
Cover distinct concrete source and target providers, including
multiple source selections with the same declared target and adapter. Matching
schemas or bare IDs must not redirect acquisition or merge correspondence scopes.
Check representative Ibis compilation for both backends without opening database
connections. Mock acquisition, clients, connections, and engines; use in-process
fakes and temporary files without live services or real database connections.
Preserve the test-suite service-access guard. Do not pin documentation wording.

## Deferred or excluded work

[Milestone 8](08-sqlite-result-storage.md) implements persistent identities and
values, [milestone 9](09-node-caching.md) implements lookup and reduced scheduling,
and [milestone 10](10-execution-reports-and-durable-success.md) reports actual
acquisition and bypass outcomes. Those milestones verify selector revision and
target-provider code and origin revision invalidation, per-location independence,
acquisition bypass, and honest execution outcomes through their existing mechanisms.

Caller-selectable operation targets, broader relational queries, additional
backends, a custom query language, separate query-result caching, independent
selector identities, and arbitrary Python I/O sandboxing are outside scope.
Operations continue to map existing registered locations. Managed GeoParquet input
acquisition does not depend on the later
[GeoParquet export milestone](12-geoparquet-exports.md).

## Reference points

Declaration work starts with [node definitions](../src/lyra/sdk/plugin.py),
[provider declarations](../src/lyra/sdk/locations.py), and
[location schemas](../src/lyra/sdk/location_schema.py). Integration uses the
[route compiler](../src/lyra/runtime/planning/catalog.py),
[executor](../src/lyra/runtime/execution.py), and
[producer validation](../src/lyra/sdk/results.py).

Ibis documents [unbound expressions](https://ibis-project.org/how-to/extending/unbound_expression),
the [DuckDB backend](https://ibis-project.org/backends/duckdb), the
[PostgreSQL backend](https://ibis-project.org/backends/postgresql), and
[distance-within predicates](https://ibis-project.org/reference/expression-geospatial#d_within).
GeoPandas documents [geometry unions](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoSeries.union_all.html)
and [spatial joins](https://geopandas.org/en/stable/docs/reference/api/geopandas.sjoin.html).
