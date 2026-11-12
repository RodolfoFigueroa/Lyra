# 7. Operation target selection with Ibis

Let operations select target candidates from a registered provider without acquiring
external resources themselves. Extend the shared executor from
[milestone 6](06-unified-graph-execution.md) with lazy Ibis filters and managed
provider adapters, then supply validated candidate records to ordinary mapping
handlers. Storage, caching, and reporting build on this execution boundary later.

## Accepted scope

### Computational operations and optional selectors

Operations compute deterministically from one source location table, optional
candidates, and optional typed parameters under milestone 6's signature contract.
They accept no metric-table dependencies or additional location inputs.
Their mapping handlers and target selectors perform no direct external I/O: no
database queries, file reads, network requests, or resource acquisition. Reject
operation resource bindings and direct external-origin declarations during
registration or plan validation. Providers and managed adapters own location
acquisition; metrics retain their declared external-acquisition contract.
Trust authors to obey the computational restriction without inspecting Python
handler code, intercepting arbitrary I/O, or introducing an execution sandbox.

An operation may declare one optional callback with `target_selector=...` for the
provider named by `target_provider=...`. Pass the resolved source GeoDataFrame as
its first positional argument and an unbound Ibis table representing that provider's
registered schema as its second. Argument names are arbitrary; the operation's
sole named source declaration remains the authority for graph bindings. The callback
returns a lazy `ibis.expr.types.Table` expression selecting target records. The
table is a symbolic relation, not an in-memory copy of the provider's collection.

[Milestone 14](14-interventions.md) extends managed access with request-specific
effective datasets. The symbolic relation then represents the target provider's
base records after removals and additions. Source tables also reflect their own
catalog's intervention set. Authors keep the same callbacks and registered schema;
the runtime applies the effective view consistently before selection and mapping.

Infer the optional parameter model from the mapping handler's keyword-only
`parameters` annotation, using the shared `MetricParameters` base. If the operation
is parameterized, both the selector and mapping handler must accept keyword-only
`parameters` annotated with the same model class, even if one callback does not
use it. Otherwise both omit that argument. Validate signatures and matching models
before acquisition; selectors cannot introduce a separate parameter model or
additional arguments. Defaults and explicit caller bindings produce one set of
effective parameter values for the operation.

The operation's sole source input and target declare concrete provider IDs under
milestone 6. Resolve target acquisition through its provider registration and
declared origins. Source and target providers may differ. Changing a source
selection does not change the target provider or its scope. Callers cannot bind
another target or make a selector choose one at execution time.

Lyra executes the expression through the target provider's managed adapter and
passes the source GeoDataFrame first and the validated candidate GeoDataFrame
second to the mapping handler, also by position with arbitrary argument names.
The sole source remains the partition owner; candidates are not a declared input.
The selector cannot depend on its own candidates or mapping result. Authors do
not call `.execute()`, `.to_pandas()`, or another materialization method on the
symbolic expression, and receive no backend connection through it.

Apply milestone 6's native-table ownership boundary to both callbacks. The selector
and mapping handler receive independent working frames; editing the selector's
inputs cannot alter the mapping handler's inputs or the runtime's original
selection. Candidate edits inside the mapping handler cannot change the allowed
target IDs or authoritative target records used for forwarding. Provider scopes,
including code revisions and origin identities, stay in runtime bindings for each
source/target role, independent of frame attributes.
Supply independent parameter instances with equal effective values to both
callbacks, including detached nested values. Selector mutations cannot change the
mapping handler's parameters or the runtime's retained values and identity.

Operations that derive target IDs from their source records need no selector,
candidate argument, or query-capable adapter. Their declared target provider
resolves returned IDs through the ordinary exact-ID path. A selector, when present,
restricts the mapping's target IDs to its selected candidates. Neither form creates
or registers locations.

### Single-provider filters

Return the supplied target relation unchanged to select all records, or apply
filters to select a subset. Both forms preserve every registered column, its value,
and the unique `location_id` of each matched record. Predicates may combine target
attributes, constants, and scalar or geometry literals computed
from the source table and effective parameters. Use ordinary deterministic row-wise
Ibis predicates, including comparisons, Boolean composition, null tests, literal
membership, and spatial predicates supported by the selected backend. Attribute
comparisons combined with geometry intersection and distance-within predicates
must work on both adapters.
Apply Ibis/backend null semantics: only a true filter predicate selects a row.

With interventions, these same predicates select both base and added records;
removed records are absent. Unfiltered selection returns the complete effective
dataset. Do not append all additions after filtering the base records. Preserve
the supported backend's spatial, attribute, and null semantics for added records.

Require a table expression as the return value; a standalone Boolean predicate
is invalid. Validate the expression's relation and shape before execution. Reject
additional relations, joins, subqueries, aggregates, windows, projections, computed output
columns, unions, sorting, limits, offsets, sampling, raw SQL, and user-defined
functions. Chained filters on the supplied relation are supported. These are
structural restrictions on ordinary Ibis expressions, not a custom query language
or a replacement expression engine. Unsupported expressions and backend operations
fail clearly instead of falling back to Python filtering or another data source.

These restrictions govern author-written selector expressions. Managed application
of intervention sets does not authorize broader selector expressions or a Python
predicate evaluator. Milestone 14 owns the internal effective-view behavior for
both managed adapters while retaining these restrictions.

The selector computes candidate membership; the mapping handler chooses each
source's actual correspondences from those candidates. A selector may acquire a
superset for the handler to refine. Authors are responsible for acquiring enough
candidates for their intended mapping and for results being independent of input
row order.

### Managed provider adapters

Provide standard adapters for Parquet/GeoParquet through Ibis's DuckDB backend with
spatial support, and PostGIS through Ibis's PostgreSQL backend. Expose them as
`ParquetLocationProvider` and `PostGISLocationProvider` subclasses of
`LocationProvider`, imported from `lyra.sdk`. Both supply exact-ID resolution and
predicate execution automatically; authors declare identity, origins, schema, and
source configuration without writing `resolve()`, a query adapter, or SQL. Both
acquisition paths use the same provider scope, source, and executable schema.
Query capability follows the provider class and is discoverable from declarations.

Provide a `PostgresIO` resource in `lyra.sdk` that owns one native Ibis PostgreSQL
backend per resource instance. Its configuration supplies host, port, database
name, and user; its `password` secret uses ordinary credential references. Lyra
owns initialization and cleanup through milestones 5 and 6; `PostgresIO.setup()`
connects through Ibis and `close()` disconnects, including cleanup after partial
setup failure. The backend uses its PostgreSQL driver without a SQLAlchemy engine
or a separate pool. Expose the initialized native backend as `.backend`. Authors
can subclass `PostgresIO` to add domain query methods.

A PostGIS provider declares
`database: Annotated[PostgresIO, Resource("city_db")]` using milestone 5's resource
dependency syntax. Declare its `db_schema` and `table` alongside its record schema;
connection settings and credentials belong to the shared database resource.
Several providers, ordinary resources, and directly querying metrics may share
that registration and its execution-scoped instance. Require a compatible
`PostgresIO` type rather than adapting arbitrary resource or engine interfaces.
The provider borrows the backend and never closes it. Each provider and directly
querying metric declares the origins it reads, including reads delegated through
other resources; sharing a connection does not supply those declarations.

`db_schema` and `table` identify one existing PostgreSQL table, view, or
materialized view. Each exposes the registered columns, unique string location
IDs, and geometry in the declared CRS. Both acquisition paths read that same named
relation as their base source. The provider does not mutate base data, create
persistent database objects, refresh materialized views, or accept SQL query
strings or transformation configuration. Authors may prepare column names,
computed attributes, or geometry in an upstream view; selectors still filter
records from one registered provider.

Ordinary acquisition creates no database objects. Milestone 14 permits
Lyra-owned temporary tables for intervention-aware managed selection. Preparing
that staging requires PostgreSQL's database `TEMPORARY` privilege in addition to
ordinary read access, without requiring base-table mutation or persistent-object
creation privileges. Requests without interventions retain ordinary read-only
acquisition. Choose temporary names that do not shadow existing relations and keep
provider base references schema-qualified. Staging ownership,
reuse, and cleanup follow milestone 14; the provider continues borrowing its
backend without closing the shared resource.

`ParquetLocationProvider` takes a string `path` through ordinary resource
configuration. It identifies one local Parquet or GeoParquet file accessible to
the executing process. Accept absolute paths and resolve relative paths from that
process's working directory. Reject directories, glob patterns, collections of
files, and remote URLs. Check file availability when acquisition is needed;
registration and discovery perform no file access. The provider owns its embedded
DuckDB backend lifecycle.

Sources for both adapters expose the registered column names, compatible types,
and expected CRS.
Prepare column renames, computed attributes, and reprojection upstream; these
adapters do not add transformation callbacks or a column-mapping language.

Selectors receive the source table, unbound target relation, and optional typed
parameters, never these resources or their backends. Lyra executes the validated
expression through the provider's backend. Managed resource use is sequential
within an execution; authors who introduce concurrent access to `.backend` or
custom query methods must synchronize it. Native backend access does not promise
automatic locking, and sharing a connection does not establish a transaction
spanning the run.

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
The registered schema also supplies the shape of empty results. Carry its declared
types through adapter bindings and intermediate tables, including empty and
all-null columns. When geometry uses a binary interchange representation, preserve
that explicit binary type even when there are no rows; value inference alone
cannot establish the schema.

Spatial comparisons require compatible geometry CRS and distance units. Source
frames retain their provider's declared CRS; the target relation and candidate
frames use the target provider's declared CRS. Lyra performs no automatic
reprojection or distance-unit conversion, including interpreting geographic degrees
as metres. Authors reproject source geometry in memory when needed, using existing
libraries in both selectors and mapping handlers. Selector geometry literals must
use coordinates and an SRID matching the target CRS; assigning an SRID alone does
not transform coordinates. Reject detectable CRS mismatches. Authors remain
responsible for meaningful units and spatial predicates.

Managed adapters own expression binding and backend-specific compilation. Bind
the validated selector to the base or effective relation while preserving its
predicates, registered columns, and values. Where the supported backend requires
a geometry literal constructor without an SRID argument, validate the literal's
SRID against the target CRS before emitting that form. Preserve its coordinates
and retain the declared CRS when decoding records. This translation supplies a
supported backend representation; it does not repair CRS mismatches, reproject
geometry, or evaluate predicates in Python.

Bind the expression to the registered source and execute filtering in the backend
before materializing candidate records into Python. Acquire the complete matching
set without implicit preview limits, pagination loss, or truncation. This does not
guarantee index use, avoidance of complete file scans, or a small matching set.
Choose and verify a compatible Ibis/backend dependency combination during
implementation; support for these adapters is not a promise that every Ibis
operation or library version works identically on both.

### Execution, forwarding, and identity

Use the same stages in local, development, and API/worker execution:

1. Resolve the operation's sole source binding and effective parameters.
2. Construct and validate the lazy target filter without external acquisition.
3. Execute it at the managed provider boundary and validate its candidate records.
4. Invoke the mapping handler with source and candidates by position, plus declared
   parameters.
5. Validate the returned mapping and forward source/target correspondence rows.

Operations still return `dict[str, tuple[str, ...]]`. Preserve supplied-source
membership checks, unique targets within each source group, shared targets across
groups, and completed empty groups. Check dictionary keys against the original
source selection. For selector-backed operations, every target ID inside the value
tuples must belong to that invocation's original validated candidate set. Reject
IDs outside that set even if they exist at the provider or are already available
elsewhere in the run. Validate membership in memory without further acquisition.
Reuse the runtime's validated candidate records when forwarding selected targets;
selector-backed mappings trigger no additional exact-ID lookup.

ID-only operations resolve returned target IDs through the ordinary managed
exact-ID path, using the effective target dataset when interventions apply. Added targets
can resolve; missing or removed requested targets remain errors. Interventions do
not rewrite returned IDs or create source-to-target relationships.

Zero-source operation requests complete successfully with an empty mapping,
without invoking the selector, acquiring targets, or invoking the mapping handler.
This skips operation candidate acquisition; it does not waive independent
intervention validation, which milestone 14 requires even for empty selections
and cached computations. That validation precedes downstream operation callbacks
and follows the normal provider resource lifecycle.

For a nonempty source request, a successful selection with no matches still invokes
the mapping handler with a schema-valid empty candidate GeoDataFrame. Authors may
accept no matches by returning empty tuples or omitting source keys, or raise an
error when their domain requires a match. Only empty groups pass mapping validation
in that case; Lyra does not complete them automatically before the handler runs.
Query, connection, decoding, validation, and mapping failures remain failures;
none are converted into a successful empty selection or partial mapping.

A provider scope contains its ID, code revision, and deduplicated origin identities.
It fixes base searchable membership and every base record's contents, including
fixed unversioned origins. Changed external contents require an updated origin identity;
output-affecting acquisition or transformation changes require an updated provider
code revision or ID. Adapter configuration must honor that scope. A revision label
does not archive a historical source snapshot.

The selector is part of the operation's implementation. Its changes follow the
same author-managed `(node_id, code_revision)` rule as mapping changes. The sole
source binding's identity, including any upstream operation provenance, effective
parameters, target provider scope, and implementation identity determine selection
and mapping together. Candidate IDs and constructed expressions need not be known
to form the operation's computation identity. Do not add expression hashes,
independent selector revisions, query cache keys, or adapter credentials to it.
Base provider record identity remains exactly
`(provider_id, code_revision, origin identities, location_id)` regardless of
acquisition path. Provider revisions cover output-affecting adapter changes;
operation revisions cover selection and mapping changes.

When interventions apply, the operation's source binding and target dependency
retain their effective dataset identities under milestone 14. The target identity
includes its base scope and intervention-set identity before candidate IDs are
known. Added records carry the set identity; unchanged base records retain their
ordinary keys. A changed target set can change selection and therefore the
operation's key even when source records and parameters are unchanged.

Full and uncached execution select candidates for the operation's complete bound
source selection. Per-location execution may select for the needed source
partitions. Its author-owned independence promise covers selection and mapping
together: another source entering or leaving a physical batch must not change a
partition's result. A shared candidate pool may grow with the batch, provided each
source's mapping remains independent of that growth.

Later caching can bypass selection and candidate acquisition on a valid operation
or downstream hit. Individual cached records do not establish complete query
membership, including absence of matches. When selection is needed, execute its
query even if some matching records are already retained; returned records can
satisfy subsequent exact-ID needs. Preserve acquisition outcomes at this boundary
for later reporting, separately from selector construction and mapping execution.

## API sketches

### Managed providers and a shared database resource

Assume `BLOCK_SCHEMA` and `TREE_SCHEMA` declare the complete provider records used
below: string `location_id` and valid non-null `geometry`, with blocks in EPSG:4326
and trees in EPSG:32614. Trees also have a numeric `height` in metres. The registered
Pandera schemas supply query types without inspecting the external sources.

```python
from typing import Annotated

from lyra.sdk import (
    Origin,
    ParquetLocationProvider,
    PluginDefinition,
    PostGISLocationProvider,
    PostgresIO,
    Resource,
)

CITY = Origin(id="city-inventory", revision="2026")


class CityDatabase(PostgresIO):
    def block_count(self) -> int:
        blocks = self.backend.table("blocks", database="public")
        return int(blocks.count().execute())


class CityBlocks(PostGISLocationProvider):
    database: Annotated[PostgresIO, Resource("city_db")]

    id = "city_blocks"
    code_revision = "1"
    origins = (CITY,)
    schema = BLOCK_SCHEMA
    db_schema = "public"
    table = "blocks"  # Existing table, view, or materialized view.


class Trees(ParquetLocationProvider):
    id = "trees"
    code_revision = "1"
    origins = (CITY,)
    schema = TREE_SCHEMA


plugin = PluginDefinition(
    resources={
        "city_db": CityDatabase,
        "block_source": CityBlocks,
        "tree_source": Trees,
    },
)
```

`CityDatabase` is compatible with the provider's `PostgresIO` requirement. Its
custom query method uses ordinary Ibis APIs on the initialized backend. A metric
declaring `origins=(CITY,)` can receive
`database: Annotated[CityDatabase, Resource("city_db")]` and call
`database.block_count()`. It shares the provider's database instance during that
run. Resource dependencies use keys such as `city_db`; provider configuration uses
`block_source` or `tree_source`. Location inputs and operation targets use public
provider IDs such as `city_blocks`.
Provider acquisition methods are supplied by the built-in classes.

Supply deployment configuration through the existing resource mapping:

```python
from lyra.sdk import ResourceConfig, SecretReference

resource_config = {
    "city_db": ResourceConfig(
        config={
            "host": "db.example.com",
            "port": 5432,
            "database": "city",
            "user": "lyra",
        },
        secrets={"password": SecretReference(env="CITY_DB_PASSWORD")},
    ),
    "tree_source": ResourceConfig(config={"path": "/data/trees.parquet"}),
}
```

The absolute `tree_source` path identifies one file on the executing process's
filesystem. For a process running from `/srv/lyra`, a relative configuration such
as `config={"path": "data/trees.parquet"}` identifies `/srv/lyra/data/trees.parquet`.
Deployment examples use absolute paths to make the source location explicit.

`block_source` needs no connection configuration of its own. `db_schema` names the
PostgreSQL namespace; `schema` is the provider's Pandera record schema. An existing
view named `public.block_locations` uses the same declaration with
`table = "block_locations"`. Creating views and refreshing materialized views
remain outside the provider.

Plain Parquet with WKB geometry uses the same `path` configuration and takes its CRS from
the registered schema. Configuration must locate the declared collection: changed
contents or membership require updated origin or provider identity as appropriate.

### Selector return values

The source argument is a resolved `gpd.GeoDataFrame`; the target argument and
return value use `ibis.expr.types.Table`, imported here as `ir.Table`.
These unparameterized selectors can be paired with unparameterized mapping
handlers:

```python
import geopandas as gpd
import ibis.expr.types as ir


def select_all_trees(sources: gpd.GeoDataFrame, trees: ir.Table) -> ir.Table:
    return trees


def select_tall_trees(sources: gpd.GeoDataFrame, trees: ir.Table) -> ir.Table:
    return trees.filter(trees.height > 5)
```

Both return lazy tables. Lyra executes the selection and gives the mapping handler
the source and candidate records as GeoDataFrames. Returning `trees.height > 5`
alone does not satisfy the selector contract.

### Selecting and mapping candidates

Assume the registered `city_blocks` provider exposes `location_id` and `geometry`
in EPSG:4326, and the `trees` provider exposes point geometry in projected EPSG:32614
and `height` in metres. Both schemas require non-null valid geometries. The target
CRS uses metre units and is appropriate for the locations in this example. Both
callbacks explicitly reproject their source geometry before comparing distances.
The operation uses milestone 6's declaration syntax with a `target_selector`
callback; the Ibis and GeoPandas calls use their ordinary library APIs.

```python
import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
from pydantic import Field

from lyra.sdk import LocationDependency, MetricParameters, location_operation


class TreeParameters(MetricParameters):
    minimum_height: float = Field(5.0, ge=0)
    maximum_distance: float = Field(100.0, ge=0)


def select_mature_trees(
    sources: gpd.GeoDataFrame, trees: ir.Table, *, parameters: TreeParameters,
) -> ir.Table:
    if sources.empty:
        return trees.filter(ibis.literal(False))
    projected_sources = sources.to_crs("EPSG:32614")
    area = ibis.literal(
        projected_sources.geometry.union_all(), type=dt.GeoSpatial(srid=32614)
    )
    return trees.filter(
        trees.height > parameters.minimum_height,
        trees.geometry.d_within(area, distance=parameters.maximum_distance),
    )


@location_operation(
    name="mature_trees_near_blocks",
    description="Associate blocks with trees meeting height and distance thresholds.",
    inputs={"blocks": LocationDependency(provider="city_blocks")},
    target_provider="trees",
    target_selector=select_mature_trees,
)
def mature_trees_near_blocks(
    blocks: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame,
    *,
    parameters: TreeParameters,
) -> dict[str, tuple[str, ...]]:
    distinct_blocks = blocks.drop_duplicates(subset="location_id")
    if candidates.empty:
        return {block_id: () for block_id in distinct_blocks.location_id}

    projected_blocks = distinct_blocks.to_crs(candidates.crs)
    matches = gpd.sjoin(
        projected_blocks[["location_id", "geometry"]].rename(
            columns={"location_id": "block_id"}
        ),
        candidates[["location_id", "geometry"]].rename(
            columns={"location_id": "tree_id"}
        ),
        how="inner",
        predicate="dwithin",
        distance=parameters.maximum_distance,
    )
    groups = matches.groupby("block_id")["tree_id"].agg(tuple).to_dict()
    return {
        block_id: groups.get(block_id, ())
        for block_id in distinct_blocks.location_id
    }
```

The declaration's source name is `blocks`. The selector calls its first argument
`sources`; positional invocation supplies the same source selection to both
callbacks. `trees` and `candidates` are ordinary argument names identifying the
second positional slot in their respective callbacks. Both accept the same
`TreeParameters` model, inferred from the mapping handler, with independent
instances carrying equal effective values. The selector uses both thresholds;
the mapping handler uses the distance to assign candidates to individual blocks.

The selector uses the target's declared EPSG:32614 for both reprojection and the
Ibis geometry literal. The mapping handler reprojects its own source frame to
`candidates.crs`, since callbacks receive independent working frames. These local
transformations preserve location IDs and leave registered source records in
EPSG:4326. Candidate and forwarded target records remain in EPSG:32614.

The union selects trees near any supplied block; the in-memory spatial join assigns
them to individual blocks. With the defaults, height must exceed five metres,
while a distance of exactly 100 metres qualifies because the target CRS uses
metre units. Trees inside a block have zero distance. One tree can appear under
several blocks, and a block without matches
returns an empty tuple. The in-memory join is ordinary computation on resolved
inputs; it is not an additional relation in the backend filter.

The empty-candidate branch accepts no matches as a successful result. An operation
that requires a match could instead raise `ValueError("No target candidates matched")`
in that branch; Lyra propagates the failure.

An operation-derived `blocks` input may contain the same block under several
incoming source locations. This mapping depends on each distinct block's geometry,
so it selects distinct blocks before joining the unique candidate records. Each
tree then appears at most once within a block's target tuple, while remaining
available under other blocks. Handlers that use incoming relationships retain
those correspondence rows; the runtime does not collapse them.

For source IDs `block_A` and `block_B` and candidate IDs `T1` and `T2`, mapping
validation distinguishes source keys from target values:

```python
{"block_A": ("T1",), "block_B": ()}  # Valid: selected target and an empty group.
{"block_A": ("T3",)}  # Invalid: T3 is outside the selected candidates.
{"block_C": ("T1",)}  # Invalid: block_C is outside the supplied sources.
```

## Completion boundary

Selector-backed and ID-only operations work through the common local and worker
runtime. Both adapters apply attribute and spatial filters before Python candidate
materialization, preserve registered schemas and revisions, and use ordinary
mapping validation and forwarding. Discovery and planning remain service-free.
Resolved sources and candidates are native GeoDataFrames; only the target query
relation remains a lazy Ibis table.
Examples and authoring guidance explain computational operations, configured
provider adapters, and ordinary Ibis filters without a second query DSL.

Check positional callback binding with names that differ from each other and from
the sole source declaration. Reject missing/extra table arguments, keyword-only
table slots, metric dependencies, and additional source inputs before acquisition.
Cover parameterized and unparameterized operations, missing or mismatched selector
parameter models, selector-only parameter declarations, forwarded values, and
defaults. Both callbacks receive equal effective parameters with isolated nested
values; mutations cannot reach the other callback or retained computation identity.

With fake backends, verify shared `PostgresIO` use by multiple providers, directly
querying metrics, and another resource. Check compatible subclasses, declaration-only
capability discovery, and reuse of the same backend for predicate and exact-ID
queries. Verify lazy connection setup, provider borrowing without premature
disconnection, and one cleanup attempt by the owning resource after success or
failure, including partial setup failure. Keep declared provider/origin identities
independent of resource keys and credentials. Cover Parquet backend ownership
separately; no check opens a real database connection.

With fake backends, cover table, view, and materialized-view sources through the
same `db_schema` and `table` fields. Exact-ID and predicate acquisition read the
same named relation and enforce the same record schema, ID uniqueness, and CRS
rules. Verify that ordinary acquisition issues no creation or refresh requests
and requires no staging privileges. Milestone 14 covers request-owned temporary
objects; base mutation, persistent-object creation, and refresh requests remain
unsupported. SQL query strings and transformation configuration are not provider
source forms.

With temporary files and fake backends, verify single-file Parquet and GeoParquet
configuration through absolute paths and paths relative to the executing process's
working directory. Both exact-ID and predicate acquisition use that same file.
Reject directories, glob patterns, file collections, and remote URLs before the
backend attempts to read them. Missing or unreadable files fail acquisition;
registration and discovery do not probe the filesystem.

Verify that returning the supplied relation unchanged selects all records, and
that combined and chained filters preserve registered columns and values. Reject
standalone Boolean predicates and the unsupported query shapes before execution.
Isolated behavioral checks cover combined attribute/spatial predicates, inside and
boundary-distance matches, height thresholds, shared targets, empty sources and
candidates, unordered inputs, invalid mappings, schema/CRS failures, unavailable
adapter capabilities, unsupported expressions, and backend errors. Exercise both
local and worker paths, validated-candidate reuse, and ordinary ID-only resolution.
Exercise differing source and target CRSs with explicit author reprojection in
both callbacks. Reject detectable CRS mismatches, including mismatched literal
SRIDs, without automatic reprojection or unit conversion. Verify that local source
transformations leave authoritative source records unchanged and that candidates
and forwarded target records retain their registered CRS.
Reject target IDs outside the selected candidates even when a provider could
resolve them or the run already holds their records. Verify that selector-backed
mapping validation and forwarding perform no additional exact-ID acquisition,
while ID-only operations retain ordinary resolution.
For empty source selections, verify successful empty mappings with no selector,
target acquisition, or mapping-handler calls. For nonempty sources with empty
candidates, verify that the handler receives the registered schema, CRS, and
effective parameters. Cover both successful empty groups and an author-raised
no-match error. Query and candidate-validation failures must propagate without
invoking the mapping handler or publishing empty success.
Verify that selector mutations do not reach mapping inputs, mapping mutations do
not alter allowed target IDs or forwarded records, and membership checks use the
original source and candidate selections.
Include operation-derived source inputs with repeated represented locations and
verify unique targets within each source group while preserving shared targets
across groups.
Cover distinct concrete source and target providers, including
multiple source selections with the same declared target and adapter. Matching
schemas or bare IDs must not redirect acquisition or merge correspondence scopes.
Check representative Ibis compilation and relation binding for both backends
without opening database connections. Preserve complete validated predicates,
including chained filters, without mutating author expressions. Cover explicit
types for empty and all-null intermediates, geometry literal emission supported by
each selected backend, and CRS rejection before any SRID metadata is omitted.
Mock acquisition, clients, connections, and engines; use in-process
fakes and temporary files without live services or real database connections.
Preserve the test-suite service-access guard. Do not pin documentation wording.

## Deferred or excluded work

[Milestone 8](08-sqlite-result-storage.md) implements persistent identities and
values, [milestone 9](09-node-caching.md) implements lookup and reduced scheduling,
and [milestone 10](10-execution-reports-and-durable-success.md) reports actual
acquisition and bypass outcomes. Those milestones verify selector revision and
target-provider code and origin revision invalidation, per-location independence,
acquisition bypass, and honest execution outcomes through their existing mechanisms.
Their checks include changed operation parameters and upstream operation identities
carried by the sole source binding.

Caller-selectable operation targets, broader relational queries, additional
backends, a custom query language, separate query-result caching, independent
selector identities, and arbitrary Python I/O sandboxing are outside scope.
Public adapter protocols, automatic integration with arbitrary SQLAlchemy engines,
shared connection pools, and resource injection into selector callbacks are also
outside this implementation. Direct metric/resource queries retain their ordinary
external-acquisition contract; the single-relation restrictions apply to selectors.
Operations continue to map locations in their effective registered datasets;
caller-supplied additions are governed by milestone 14. Managed GeoParquet input
acquisition does not depend on the later
[GeoParquet export milestone](13-geoparquet-exports.md).

## Reference points

Declaration work starts with [node definitions](../src/lyra/sdk/plugin.py),
[provider declarations](../src/lyra/sdk/locations.py), and
[location schemas](../src/lyra/sdk/location_schema.py). Integration uses the
[route compiler](../src/lyra/runtime/planning/catalog.py),
[executor](../src/lyra/runtime/execution.py), and
[producer validation](../src/lyra/sdk/results.py).
Shared backend ownership builds on [resource declarations](../src/lyra/sdk/resources.py)
and the [resource lifecycle](../src/lyra/runtime/resources.py).

Ibis documents [unbound expressions](https://ibis-project.org/how-to/extending/unbound_expression),
the [DuckDB backend](https://ibis-project.org/backends/duckdb), the
[PostgreSQL backend](https://ibis-project.org/backends/postgresql), and
[distance-within predicates](https://ibis-project.org/reference/expression-geospatial#d_within).
GeoPandas documents [geometry unions](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoSeries.union_all.html)
and [spatial joins](https://geopandas.org/en/stable/docs/reference/api/geopandas.sjoin.html).
