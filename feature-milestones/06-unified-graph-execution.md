# 6. Unified graph execution

Make one runtime execute location-scoped, unpartitioned, and hybrid graphs with
explicit table identities and dependency selection. Providers and handlers exchange
native tables, while location operations return correspondence mappings. They share
planning, execution, resources, validation, and the result paths. Later storage,
caching, and reporting extend this runtime rather than introducing parallel executors.

## Accepted scope

### Managed requests and routing

`runner.run(name, arguments)`, `runner.run_development(name, arguments)`, and
`client.jobs.submit(name, arguments)` accept the same plain JSON-compatible
argument dictionary. Each named location input is a top-level entry with
`{"provider": str, "ids": list[str]}`, selecting a registered source provider and
requested IDs. Calculation parameters go under the reserved `"parameters"` key;
omitted values follow the selected metric's parameter schema and defaults.
Local, development, and remote execution apply the same selection and parameter
validation.

Providers supply geometry and attributes; declared upstream nodes supply table
dependencies. External tabular acquisition
belongs to providers and metrics with declared origins; operations perform no
direct external I/O. Direct provider inputs contain location records;
operation-derived inputs contain correspondence rows joined to target records.
The runtime retains their provider scopes and source/target bindings.

A registered location is an immutable record identified by
`(provider_id, code_revision, origin identities, location_id)`. The provider scope
contains the first three components, with deduplicated unordered origin identities.
An omitted origin revision identifies fixed unversioned data. Provider exact-ID
resolvers accept only requested strict string IDs and return one schema-valid
record per resolved ID, using their declared origins. They have no arbitrary
calculation parameters or upstream-table arguments. Missing requested records
fail acquisition. An upstream operation can select records without becoming part
of their identity.

`run()` selects public metrics. `run_development()` selects concrete metrics,
including private roots, before shared request preparation. Both share selection
normalization, parameter and plan validation, provider acquisition, dependency
resolution, resources, and result validation. Subsequent storage, caching, and
reporting use these same paths. Public discovery and remote root submission remain
restricted to public metrics; declared dependencies execute normally.

For location inputs, preserve shortest-route discovery across installed
plugins, explicit preferences for ties, ambiguity errors, and distinct operation
and provider identities. Routes connect the selected source provider to the input's
declared provider requirement. Requests do not enumerate conversion sequences.
Matching source and required providers use the zero-operation route.

Operations opt into automatic routing with `canonical=True`; the default is
`canonical=False`. Both remain available through explicit
`LocationDependency(operation=...)` invocations. A canonical operation has exactly
one source location input, and its dependencies and required parameters must be
satisfiable from that source and declared bindings/defaults without additional
caller inputs. Reject invalid canonical declarations before acquisition. A primary
marker on one of several location inputs does not make an operation eligible.
Registration alone does not authorize implicit use of an operation.

Declare tie preferences through `PluginDefinition(route_preferences=[...])`.
Each preference is a nonempty tuple of decorated operation references in traversal
order, identifying one complete route. Referenced operations must be registered
and canonical, form a connected route, and have the shortest length for its
endpoints. Preferences resolve equal-length alternatives; they do not override
shortest-route selection. Reject conflicting preferences and unresolved shortest
route ties rather than choosing by registration order. Different operations between
the same providers can select different locations, so provider endpoints alone do
not establish interchangeable behavior.

Output scope and later reporting do not filter routes or change tie resolution.
The executor validates the complete plan before resources or handlers, including
unknown named inputs/outputs and invalid bindings; reporting adds no alternative
eligibility classification.

Build the graph directly from the validated live dependency declarations established
in [milestone 4](04-plugin-discovery-and-normalized-jobs.md). Resolve them into
runtime bindings for particular node uses without converting through a parallel
manifest representation. Preserve named inputs and outputs, parameter forwarding,
nested operation bindings, and their caller scope. Derive input selection from
these resolved bindings, primary-input choices, and declared operation
correspondences.

Collect origins directly from metric and provider declarations and deduplicate
by `(id, revision)` before cache lookup or resource initialization. Providers'
managed adapters use those same declared origins. Every direct external acquisition
must be declared, including in unpartitioned metrics and with caching off. Resources
provide I/O configuration and lifecycle; data identities belong to node declarations.
Reject resource bindings and direct-origin declarations on operations. Their
computational contract is author-owned; plan validation does not inspect Python
code or enforce an I/O sandbox.

### Concrete location inputs and bindings

Declare all handler table inputs together in the node's `inputs` mapping.
`LocationDependency(provider=...)` declares a location input's required provider
ID and derives its record schema from that registration. `Dependency` selects a
metric's named output. These declarations distinguish location records from metric
tables independently of the handler's native table annotations.

`LocationDependency` uses exactly one of the explicit `provider=` or `operation=`
forms. The operation form invokes a registered operation whose declared target
determines the resulting provider. Use `.bind()` on metric and operation
invocations to supply named location arguments, with the caller-scoped reference
and parameter rules from [milestone 4](04-plugin-discovery-and-normalized-jobs.md).

A managed request supplies a source provider ID and location IDs for each named
input. Resolve the source provider and route to
the declared requirement when needed. Different named inputs may require different
providers. Validate bindings and required routes before resource acquisition.

Forward resolved provider scopes and location IDs through existing named
dependency bindings, applying the receiving input's concrete provider requirement.
Preserve matching-name forwarding and explicit overrides. Different uses of the
same metric in one graph may bind different ID selections or parameters while
retaining its declared provider requirements. Runtime memoization distinguishes
those uses by their bound computation identities.

Resolve each named input's provider scope before the applicable computation
key and cache lookup, retaining scope even for empty selections. Binding uses
declarations and request selections without acquiring records merely to resolve
metadata. Operations may still discover target IDs during ordinary execution;
their target provider and its scope are fixed by declaration and known beforehand.
Operation targets, independent inputs, and external origins retain their declared
identities.

Provider boundaries validate records against their registered Pandera schemas.
Operation forwarding derives its schema from target records and source/target
scopes as specified below. Validate input bindings against these declarations
without rerunning the provider's entire validation contract or acquiring inputs
solely to repeat checks for a valid cached computation. Authors own semantic
correctness.

For example, `tract_population_density(tracts)` can feed
`tract_density_classification(tracts, density)` with both nodes declaring
`census_tracts` for `tracts` and forwarding the same concrete selection.
Each result belongs to those selected tracts, qualified by the provider scope.

### Native tables and runtime ownership

Supply resolved location records and operation-derived correspondence tables as
ordinary `geopandas.GeoDataFrame` inputs. Spatial metric tables use the same type;
nonspatial metric tables use `pandas.DataFrame`. Providers return GeoDataFrames,
metrics return their declared native tables, and operations return the mappings
specified below. Explicit named declarations determine each input's role, schema,
and binding; a GeoDataFrame annotation does not make a metric dependency a
registered location input. No custom table wrapper or DataFrame subclass is needed.

Keep authoritative records, schemas, provider scopes for each source/target role,
and original selections in runtime-owned state. A small internal structure
may associate these descriptions with native tables without implementing another
table API. Handlers use columns, geometry, and CRS directly. Do not add a handler
metadata-inspection interface or use `DataFrame.attrs` to establish execution
identity, ownership, or dependencies.

Apply milestone 4's explicit ownership boundaries to metadata and parameters too.
Handlers receive independent mutable parameter values; retained effective values
and resolved bindings remain private for execution, computation identity, and
reporting. Internal metadata reads use ordinary field access without implicit
deep copies.

Give each handler table argument an independent working frame using explicit copies.
Handlers may change columns, IDs, geometry, CRS, or attributes during computation;
those changes affect only their working data. Separate named inputs and consumers
must not share mutable working frames. Preserve the authoritative records for
forwarding, cache use, persistence, and other consumers. Selector and mapping
callbacks introduced in milestone 7 also receive independently isolated inputs.
Copy isolation must not depend on optional pandas copy-on-write settings.

Capture the original qualified selection before invoking author code. Output
ownership, operation mapping keys, and correspondence membership are checked
against that selection, even if a handler edits its input IDs. Handler changes
cannot register locations or update provider records. Detach producer-returned
frames before validation and retain validated outputs privately, so later changes
through references held by producers or callers cannot invalidate stored results
or bypass validation. Apply this ownership boundary to ordinary metric tables too.

Remove the public `Locations` type, its factories and conversion methods, and
location-specific artifact wrappers across construction, execution, and inspection
paths. Ordinary library readers produce native tables for standalone files;
managed retrieval keeps result and scope descriptions separate from returned
frames. Remove `LocationContext.resolve_locations()` and its acquisition adapter:
operations get data through declared inputs and managed target selectors.
Examples and authoring guidance use the native interfaces directly.

Make this a clean public API replacement without aliases, shims, dual interfaces,
version negotiation, migrations, or historical readers. Keep current execution
and result access usable at completion. The later storage milestone replaces
internal snapshot persistence and content identity without exposing them through
the author-facing table contract.

### Structured local run results

On success, `runner.run()` and `runner.run_development()` return a public
`RunResult` object. Its `.tables` mapping contains the root metric's named native
DataFrames or GeoDataFrames. Its `.metadata` is a structured description of that
execution. `.metadata.metric_name` identifies the selected concrete root metric;
`.metadata.parameters` contains its effective parameter values, including defaults
and nulls. Named output descriptions are accessible through
`.metadata.outputs[name]`. At return, the table and output-description mappings
contain exactly the declared output names.

Each output description exposes `scope`, `table_schema`, and `owner_scope`.
`scope` is `"location"` or `"unpartitioned"`, derived from the metric's primary-input
declaration. `table_schema` reuses the descriptive schema contract used for
discovery, including logical types, nullability, descriptions, units and applicable
CRS. For a location-scoped output, `owner_scope` exposes `provider_id`,
`code_revision`, and `origins`: the owning provider's ID, code revision, and
deduplicated declared origin identities used by this execution. These describe the
scope of the returned result even if the active provider registration later changes.
An unpartitioned output has `owner_scope=None`, including when its calculation
consumes location inputs. Empty outputs retain their declared schemas and scopes.

Keep the tables and metadata together for the particular run that produced them.
Starting another run on the same runner cannot replace or alter an earlier
`RunResult`. Detach returned frames and nested metadata from runtime-owned state,
retained definitions, and other results. Caller mutations cannot alter retained
values or later reads; editing a returned table does not recompute its accompanying
metadata. Field access follows milestone 4's ordinary explicit-copy boundaries.

Access tables through `result.tables[name]`. The result object belongs to the
caller-facing execution boundary: metric handlers return native tables or named
table dictionaries, and dependency arguments remain native frames. Authors do not
construct `RunResult` inside their handlers. The returned object supplies its own
metadata without consulting the runner's latest inspection state. Durable execution
identification and report access extend this structure in milestones 10 and 11.

### Output scopes and identities

Derive one output scope for all of a metric's named outputs from its own `inputs`
declarations. With no location input marked `primary=True`, the metric is
unpartitioned, whether it has zero, one, or several location inputs. Exactly one
`LocationDependency` marked `primary=True` makes the metric location-scoped and
identifies the input whose locations own its output rows. Reject multiple primary
designations and primary markers on ordinary metric-table dependencies. Even a
metric with a single location input requires the explicit marker to opt into
location ownership. Output columns and cache settings do not determine scope.

Primary ownership belongs to the receiving node's input declaration and cannot
be overridden by a nested invocation binding. Location operations always
partition by a primary source input: infer it when there is one location input;
with several, require exactly one marked `primary=True`. Operations require at
least one location input. Providers partition by their own records.

Each metric has one implementation, a schema for each named output, a derived
scope, and a cache policy. Retain that scope in runtime, storage, and reporting
metadata. Location-scoped metrics also retain one primary-input choice. That
input's provider scope qualifies output ownership for each bound use;
the original selected IDs determine eligible owners.

Both scopes retain declared graph dependencies, parameters, origins, computation
identities, and normal execution reporting. Scope determines output ownership
and input-selection behavior; it does not hide the computation's dependencies.

The fixed handler-visible structure is:

| Table | Required structural columns | Row identity within its table scope |
| --- | --- | --- |
| Provider records | `location_id`, `geometry` | Unique `location_id` |
| Operation-derived location input | `source_location_id`, `location_id`, `geometry` | Unique source/target pair |
| Location-scoped metric output | `location_id`, `row_key`; `geometry` when spatial | Unique owner/row-key pair |
| Unpartitioned metric output | `geometry` when spatial; no mandatory identity columns | No author row identity required |

Location IDs and metric row keys are non-null strings. Provider scope metadata
qualifies each location-ID role; operation inputs carry separate source and target
scopes. A metric row key is additionally scoped to its named output. Consumers
address rows through the named input and those qualified identities, preserving
them across execution and later cache assembly without historical invocation links.

Every location-scoped metric output declares non-null string `location_id` and
`row_key` in Pandera with composite uniqueness. Its owner must be a distinct
represented `location_id` actually supplied through the primary input, qualified
by that input's provider scope. Multiple rows may share an owner, and an
owner may produce zero rows. The same row key may appear under different owners
or named outputs. Keys are stable across input permutations and permitted batching;
indexes and row positions are not identities. Authors encode numeric or composite
row identifiers as stable, unambiguous strings.

For example, `(A, "roof")`, `(A, "entrance")`, and `(B, "roof")` are distinct
rows in one findings output; two `(A, "roof")` rows there are invalid. If a
successful invocation receives A/B and returns only B's roof finding, A completed
with no rows. Ownership refers to the distinct locations actually supplied to that
primary input, not other records exposed by the provider or elsewhere in the
request. Check it against the runtime's original selection without another provider
lookup or trust in handler-mutated frames. Different named inputs can use the same
provider while serving different roles, so provider identity alone cannot select
a primary.

An operation-derived primary input partitions by its represented target
`location_id`, retaining distinct incoming correspondence rows. Its
`source_location_id` does not change the downstream owner's scope. Nonstructural
columns, including domain-named IDs of other locations, remain ordinary data;
they create no associations, acquisitions, or dependency edges.

Handlers distinguish calculations over distinct represented locations from
calculations over correspondence rows. A calculation using only each location's
record can select distinct `location_id` values within its named input: repeated
targets there share one provider scope and identical record contents. A calculation
that uses source/target relationships retains the relevant pairs and assigns
distinct stable row keys when returning several rows for one owner. The runtime
preserves correspondence rows for those calculations; it does not deduplicate
handler inputs by target ID.

Unpartitioned metrics have no designated primary input and may have no location
inputs. Outputs are ordinary named tables without mandatory owners or author row
keys. Each invocation receives its complete bound selections,
which may be subsets of provider collections. All outputs use whole-computation
completion. Mixed output scopes within one metric are unsupported; separate metrics
provide that composition. Unpartitioned metrics permit only `off` or `full` caching;
reject `per_location` during definition or plan validation before resource
acquisition. The complete reuse behavior is delivered in milestone 9.

Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for their
structural roles. Every spatial table uses active `geometry`; nonspatial outputs
need none. Scopes belong to named input/output metadata, not repeated fields in
every row. Authors rename source fields before return; reject collisions and
aliases in place of required columns without automatic suffixing or overwrites.
Unpartitioned outputs may use domain-named ID columns as ordinary values without
acquiring ownership semantics. Missing structural declarations on a location-scoped
metric are invalid definitions, never a reason to infer unpartitioned scope or
substitute row numbers.

### Operation correspondences and forwarding

Declare operations with `@location_operation(name=..., description=..., inputs=...,
target_provider=..., canonical=False)`. The unified `inputs` mapping accepts
location declarations and metric dependencies. Infer a sole location input as
primary; with several,
require exactly one marked `primary=True`. The required `target_provider` names
one registered provider and is fixed by the
operation declaration; source and target may use the same provider.

Operations return `dict[str, tuple[str, ...]]` directly. Keys belong to the supplied
primary input; each tuple contains unique target IDs from the declared target
provider scope. Repeated targets under different keys are valid. Strict Pydantic
validation owns mapping types; JSON objects with target arrays restore through
the declared type. Authors supply no additional output table, DataFrame schema,
group-dependency declarations, lineage payload, or reporting wrapper. Ordinary
named input bindings still declare the operation's dependencies.
Operation source inputs and targets declare concrete provider IDs. Callers cannot
override the target through a source selection or a target-binding argument.
Authors supply the mappings; Lyra does not infer correspondences from equal IDs
or geometry. Operations may perform spatial calculations internally to determine
which existing locations correspond, without creating or registering new locations.

[Milestone 7](07-operation-target-selection-with-ibis.md) extends this path with an
optional `target_selector=...` callback. Lyra supplies the selector's
source/dependency inputs and a symbolic target table, executes its lazy filter through a managed provider
adapter, and supplies validated candidates before invoking the mapping handler.
Candidates are context, not an additional primary-input choice. Both callbacks
remain computational. Operations deriving IDs directly require no selector.

A successful return covers all supplied keys. Omitted keys and empty tuples both
mean completed zero-match groups. Unsupplied keys and duplicates within a tuple
are errors. Targets must resolve at the provider acquisition boundary; unresolved
IDs fail ordinary acquisition rather than disappearing or becoming zero matches.
No separate preflight existence lookup is required.

Resolve each distinct needed target once, reusing validated candidate records when
available after milestone 7, and join by explicit IDs. Forward one row per
`(source_location_id, location_id)` pair, with the target's geometry and
attributes. A shared target appears under every source selecting it; authoritative
provider records remain unique. Zero-match groups produce no forwarded rows or
fabricated geometry, but retain completion where execution requires it.

For a mapping `{"A": ("X", "Y"), "B": ("Y",), "C": ()}`, the forwarded
rows are:

| `source_location_id` | `location_id` | Geometry and attributes |
| --- | --- | --- |
| A | X | X's record |
| A | Y | Y's record |
| B | Y | Y's record |

C has a completed empty group and no forwarded row. Omitting C from the returned
mapping has the same meaning after successful return. Both Y rows remain visible
to a batched consumer, but there is only one authoritative Y record. The pair is
the relationship identity; the target retains its separate location-data identity.
No row order is promised by this example.

Lyra derives forwarding schemas from the target provider schema and source/target
scopes. Preserve attribute types, nullability, descriptions, units, and target CRS,
including for empty tables. Add non-null string source IDs and pair uniqueness.
Provider uniqueness and cardinality checks apply before expansion, not unchanged
to repeated target rows. Reuse validated values and enforce construction invariants
without translating arbitrary Pandera checks or revalidating provider tables.
Operation attributes come from resolved provider records, not a second operation
payload. An entirely empty forwarding table still has both ID columns, target
geometry and attribute types, both scopes, and target CRS.

Each hop retains its immediate meanings: A → B → C forwards B/C pairs with C's
records at the final hop. Consumers needing A/C associations declare the relevant
inputs and explicitly compose qualified mappings. Selection may follow declared
chains without relabeling intermediate IDs or expanding every possible path.

### Input selection and unordered collections

Input selection determines which bound rows a consumer receives. Derive it
automatically from the resolved graph, independently of cache policy. The primary
input follows the locations requiring computation. A dependency follows those
locations when its resolved binding connects the consumer's primary input to
the producer's primary input, directly or through declared operation
correspondences and chains. These partition-related inputs supply the associated
rows for the needed primary locations.

Resolve explicit bindings and matching-name forwarding before determining that
relationship. Both can establish the same primary-input connection; coincidental
names, providers, schemas, or IDs do not establish one. A connection only to a
producer's non-primary input does not establish partition following. Other inputs
are shared: they retain their complete originally bound selection, not the
provider's entire collection. This also applies to independent inputs whose
provider or selected IDs overlap the primary input. Required inputs, bindings,
provider requirements, and routes still undergo ordinary plan validation.

Unpartitioned outputs always remain complete shared tables, including when their
inputs are bound to the consumer's primary locations. Whole-selection calculations
use an unpartitioned dependency. Hybrid graphs preserve the complete initially
bound context of an unpartitioned metric, even when downstream work later narrows
through caching.
Consumers may perform their own calculations or filtering on the supplied whole
table; the restriction is on implicit runtime selection by location.

For example, per-location building measurements can feed an unpartitioned summary
that a per-location flagging metric receives as shared context. The summary keeps
its complete initially bound selection. Changing that selection changes its
computation identity and therefore dependent identities; it cannot be sliced by
matching output IDs.

Selection and producer execution are separate contracts. When cache reuse later
reduces consumer work, an upstream `off` or `full` metric/operation retains its
complete initially bound inputs; only a `per_location` producer promises the
independence needed for a reduced request. A partition-related consumer still
receives only its associated rows. Provider exact-ID resolution may acquire the
reduced records needed under either cache setting, while shared inputs remain
complete. These rules constrain the graph now; persistent reuse scheduling is
implemented later.

Declared relationships permit selective acquisition and computation. Keeping
other inputs complete can require more acquisition, upstream work, and memory;
their complete identities also participate in each dependent computation key.
Changing that context can therefore prevent reuse even when a particular output
would be unchanged. The runtime does not infer narrower dependencies by inspecting
handler code.

Selections, target groups, partition groups, table rows, dictionary iteration,
and column positions carry no semantic ordering guarantee. Associate records by
qualified IDs and row keys and address columns by name. Handlers produce the same
logical result under input permutations. Meaningful sequence is explicit data,
such as rank or timestamp text, or ordered parameter values. Internal sorting
does not establish a public ordering promise.

Ownership, correspondence, and resolved input bindings preserve the relationships
needed for execution. They do not identify the exact rows that contributed to a
particular value or create a finding-level explanation contract.

### Validation and completion

Providers and metrics declare structural and ordinary columns together in Pandera
`DataFrameSchema` or `DataFrameModel`. Schema helpers may compile declarations but
must not require a custom location value. Registration rejects missing or weakened
structural constraints, unsupported column types, and reserved-name collisions
before execution. Spatial
schemas declare geometry type, nullability, validity, and CRS. Authors may add
compatible constraints.
Provider schemas require unique non-null string `location_id` and the expected
active geometry/CRS. Location-scoped metric schemas require non-null string
`location_id` and `row_key` with composite uniqueness. Derived operation schemas
require non-null source and target IDs with pair uniqueness while permitting
repeated targets. Unpartitioned metrics still require ordinary named-output,
schema, and supported-type validation.

Require provider and metric-output schemas to have `strict=True`; registration
rejects `strict=False` and `strict="filter"`. For `DataFrameModel`, check the
effective schema, including inherited `Config.strict` settings. Columns must have
fixed string names and be required; reject optional or regex column declarations and
automatic missing-column insertion. Nullable values remain supported in declared
nullable columns, but those columns must be present even in empty tables. These
requirements establish the complete column set before execution without imposing
column order.

Compile a storage description once from each registered schema, without database
access or a second author-provided SQL schema. Supported columns are strings,
signed 64-bit integers (`-2**63` through `2**63 - 1`), finite 64-bit floats,
booleans, and geometry, with declared nullability. Missing values follow that
nullability; non-missing floats must be finite. Temporal and categorical values
use supported strings or numbers; nested objects and lists are not table-column
types. The persistent representation
uses typed SQLite scalar columns and WKB geometry cells, with explicit pandas
dtypes and declared CRS on reconstruction. Implementing that value store belongs
to [milestone 8](08-sqlite-result-storage.md); registration itself performs no
database access.

Lyra automatically validates each result at one shared producer boundary, including
Pandera checks and explicitly enabled coercion, mapping normalization, and invocation
membership. Authors declare schemas and return native tables or operation mappings;
they need not call `validate()` in their handlers.
Metrics with one declared output return its native frame directly. Metrics with
multiple outputs return a dictionary containing exactly the declared output names
and their native frames. Empty outputs use explicit schema-valid tables; omitting
an output or returning `None` is not empty completion. Returned tables contain
exactly the declared columns with unique column names; extra, missing, or duplicate
columns fail before downstream use or persistence. Writers consume owned validated
data and matching descriptions without repeating validation; cache reads and
arbitrary public inputs retain independent boundary checks. Mutation or alternate
entry paths cannot bypass required validation.

Successful location-scoped completion covers every supplied primary partition in
every named output, including zero rows. Failed or unprocessed work is not empty
success. Empty needed ID selections invoke no provider resolver. Zero-location
work does not fabricate rows, partitions, or invocations. Needed unpartitioned metrics
execute on empty inputs unless valid full reuse supplies the result: a summary
may legitimately return a nonempty count-of-zero table.

Authors own semantic correctness, intended ownership, determinism, stable keys,
complete origins/dependencies, and the independent-computation promise required
by per-location caching. Structural checks do not prove those properties.
Trust authors without behavioral proof runs or automatic cache-policy changes.
All executable nodes operate deterministically over frozen inputs, including when
caching is disabled. Output-affecting changes must be represented in declared
implementation/data identities or effective parameters. A structurally valid row
can still have an unintended supplied owner; Lyra cannot infer that semantic error
or inspect handler code to prove complete source declarations.

The derived output scopes, unified `inputs` declarations, `primary=True` marker,
`.bind()` syntax, `@location_operation` declaration with `target_provider` and
`canonical`, plugin `route_preferences`, native handler table types, structural
column names, `RunResult.tables` and `.metadata`, and validation and ordinary
return contracts above are fixed, including the metadata field names;
no reporting flag changes handler returns.

## API sketches

### One output and managed execution

Assume the `city_blocks` provider declares unique `location_id` values and valid
Polygon/MultiPolygon geometry in EPSG:32614, measured in metres. This metric
requires that provider and uses GeoPandas directly. It opts into location ownership
by marking its `blocks` input `primary=True`. Its input record schema comes from
the registration. Declarations
and requests use the contracts above. Resolved provider scopes and selected IDs
remain runtime responsibilities.

```python
import geopandas as gpd
import pandas as pd
import pandera.pandas as pa

from lyra.contracts.units import Unit
from lyra.sdk import LocationDependency, metric


AreaOutputSchema = pa.DataFrameSchema(
    {
        "location_id": pa.Column(
            str, nullable=False, description="Area identifier", metadata={"unit": None}
        ),
        "row_key": pa.Column(
            str, nullable=False, description="Result identifier", metadata={"unit": None}
        ),
        "area_m2": pa.Column(
            float,
            nullable=False,
            checks=pa.Check.ge(0),
            description="Polygon area",
            metadata={"unit": Unit.SQUARE_METRE},
        ),
    },
    unique=["location_id", "row_key"],
    strict=True,
    unique_column_names=True,
)


@metric(
    name="block_area",
    description="Calculate each city block's area.",
    inputs={
        "blocks": LocationDependency(provider="city_blocks", primary=True),
    },
    outputs={"area": AreaOutputSchema},
)
def block_area(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    distinct_blocks = blocks.drop_duplicates(subset="location_id")
    return pd.DataFrame({
        "location_id": distinct_blocks["location_id"],
        "row_key": "area",
        "area_m2": distinct_blocks.geometry.area,
    })
```

With a runner configured for this metric and provider, select the blocks to measure:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]},
}
result = runner.run("block_area", arguments)
area = result.tables["area"]
```

This call supplies a registered selection. Each selected block owns one row with
the stable key `area`, qualified by the `city_blocks` provider scope. Lyra
validates the returned table against its output schema and the original selected
IDs. The handler receives all registered record columns in a native GeoDataFrame.
If an operation supplies the same block under several source locations, this
calculation measures that block once, keeping `(location_id, "area")` unique.

The same dictionary can be used for development execution or remote submission
through an open consumer client. Each call starts a separate execution:

```python
development_result = runner.run_development("block_area", arguments)
area = development_result.tables["area"]
handle = client.jobs.submit("block_area", arguments)
```

Development execution additionally permits private metric roots. For a metric
that declares a `threshold` parameter, a request can include both selections and
calculation parameters:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1"]},
    "parameters": {"threshold": 0.5},
}
```

### Unpartitioned metrics by default

A summary can consume location records and return an ordinary table. With no
input marked `primary=True`, it is unpartitioned even though it has a single
location input:

```python
BLOCK_COUNT_SCHEMA = pa.DataFrameSchema(
    {
        "block_count": pa.Column(
            "int64", nullable=False, checks=pa.Check.ge(0),
            description="Number of selected blocks", metadata={"unit": Unit.COUNT},
        ),
    },
    strict=True,
    unique_column_names=True,
)


@metric(
    name="count_blocks",
    description="Count the distinct selected blocks.",
    inputs={"blocks": LocationDependency(provider="city_blocks")},
    outputs={"summary": BLOCK_COUNT_SCHEMA},
)
def count_blocks(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "block_count": pd.Series([blocks["location_id"].nunique()], dtype="int64"),
    })
```

The output has no `location_id` or `row_key`. The handler receives the complete
bound selection, and an empty selection produces one summary row with a count of
zero. The declared inputs and provider scope remain part of its computation
identity and reporting. Downstream consumers receive the complete summary table.
Caching defaults to `off`; `full` is available under milestone 9, while
`per_location` requires location scope and its separate independence promise.

### Hybrid graphs with a whole-selection summary

Use the existing `block_area` metric to calculate an unpartitioned mean, then
compare each block's area against that summary. The schema declarations are
omitted here: `MEAN_AREA_SCHEMA` declares a nullable float column `mean_area_m2`;
`FLAGS_SCHEMA` declares non-null string `location_id` and `row_key`, unique
together, plus a non-null Boolean `is_large`.

```python
from lyra.sdk import Dependency


@metric(
    name="mean_block_area",
    description="Calculate the mean area of the selected blocks.",
    inputs={
        "blocks": LocationDependency(provider="city_blocks"),
        "areas": Dependency(producer="block_area", output="area"),
    },
    outputs={"summary": MEAN_AREA_SCHEMA},
)
def mean_block_area(
    blocks: gpd.GeoDataFrame,
    areas: pd.DataFrame,
) -> pd.DataFrame:
    return pd.DataFrame({
        "mean_area_m2": pd.Series(
            [areas["area_m2"].mean()],
            dtype="float64",
        ),
    })


@metric(
    name="flag_large_blocks",
    description="Flag blocks whose area exceeds the selected blocks' mean.",
    inputs={
        "blocks": LocationDependency(provider="city_blocks", primary=True),
        "areas": Dependency(producer="block_area", output="area"),
        "summary": Dependency(producer="mean_block_area", output="summary"),
    },
    outputs={"flags": FLAGS_SCHEMA},
)
def flag_large_blocks(
    blocks: gpd.GeoDataFrame,
    areas: pd.DataFrame,
    summary: pd.DataFrame,
) -> pd.DataFrame:
    mean_area = summary["mean_area_m2"].iat[0]

    return pd.DataFrame({
        "location_id": areas["location_id"],
        "row_key": "above_mean",
        "is_large": areas["area_m2"] > mean_area,
    })
```

Both functions declare `blocks` to establish the location binding forwarded to
their dependencies, even though their calculations use the supplied metric tables.
Matching-name forwarding supplies the selections without explicit `.bind()` calls.
Only `flag_large_blocks` marks its location input primary, so the summary is
unpartitioned and the flags belong to individual blocks. With these metrics and
their provider registered, the request remains ordinary:

```python
flags = runner.run(
    "flag_large_blocks",
    {"blocks": {"provider": "city_blocks", "ids": ["A", "B", "C"]}},
).tables["flags"]
```

If `per_location` caching in milestone 9 leaves only B to compute, the final
handler receives B's area and the complete summary for A/B/C. That summary is never
narrowed to B. Changing its bound selection changes the summary's computation
identity and invalidates dependent cached flags. For an empty selection, the
summary contains one row with a null mean, and the flags output is empty.

### Multiple outputs and stable row identities

A block inspection metric returns findings and spatial inspection points. Both
outputs belong to the supplied blocks and have independent owner/key uniqueness.
The point schema extends the findings schema with geometry; reuse the imports
above and add Pandera's geometry type:

```python
from pandera.engines.geopandas_engine import Geometry


FINDINGS_SCHEMA = pa.DataFrameSchema(
    {
        "location_id": pa.Column(
            str, nullable=False,
            description="Owning block", metadata={"unit": None},
        ),
        "row_key": pa.Column(
            str, nullable=False,
            description="Finding identifier", metadata={"unit": None},
        ),
        "message": pa.Column(
            str, nullable=False,
            description="Finding description", metadata={"unit": None},
        ),
    },
    unique=["location_id", "row_key"],
    strict=True,
    unique_column_names=True,
)

INSPECTION_POINTS_SCHEMA = FINDINGS_SCHEMA.add_columns({
    "geometry": pa.Column(
        Geometry(crs="EPSG:32614"),
        nullable=False,
        checks=[
            pa.Check(lambda s: s.geom_type.eq("Point"), name="point_geometry"),
            pa.Check(lambda s: s.is_valid & ~s.is_empty, name="valid_geometry"),
        ],
        description="Inspection position",
        metadata={"unit": None},
    ),
})
```

The calculation functions below are ordinary author-owned functions whose bodies
are omitted. They return frames satisfying the corresponding schemas:

```python
@metric(
    name="block_findings",
    description="Identify block issues and inspection positions.",
    inputs={
        "blocks": LocationDependency(provider="city_blocks", primary=True),
    },
    outputs={
        "findings": FINDINGS_SCHEMA,
        "inspection_points": INSPECTION_POINTS_SCHEMA,
    },
)
def block_findings(blocks: gpd.GeoDataFrame) -> dict[str, pd.DataFrame]:
    findings = calculate_findings(blocks)
    inspection_points = calculate_inspection_points(blocks, findings)
    return {
        "findings": findings,
        "inspection_points": inspection_points,
    }
```

`findings` is a DataFrame and `inspection_points` is a GeoDataFrame. GeoDataFrame
is a DataFrame subclass, so the return annotation covers both. The explicit
`primary=True` marker assigns both outputs to the supplied blocks.

For supplied blocks A, B, and C, a valid findings table could contain:

| `location_id` | `row_key` | `message` |
| --- | --- | --- |
| A | drainage | Inspect drainage |
| A | access | Inspect access |
| B | drainage | Inspect drainage |

C completed with no findings. Repeating `(A, drainage)` within this output fails
validation. Keys are stable domain identifiers independent of row order; the
inspection output has its own key namespace. Matching keys across outputs do not
automatically establish a relationship.

### Empty outputs and structured result access

Construct empty outputs with declared columns, dtypes, and spatial metadata:

```python
empty_findings = pd.DataFrame({
    "location_id": pd.Series(dtype="string"),
    "row_key": pd.Series(dtype="string"),
    "message": pd.Series(dtype="string"),
})

empty_inspection_points = gpd.GeoDataFrame(
    empty_findings.copy(),
    geometry=gpd.GeoSeries([], crs="EPSG:32614"),
)
```

A successful handler can return either or both empty frames under their declared
output names. Each supplied block completes independently in each output,
including outputs where it produced no rows. All declared names remain required.
The single-output `block_area` handler returns its frame directly; the local
runner returns `RunResult` with named tables and their execution metadata. With
these metrics and their provider registered:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]},
}
area_result = runner.run("block_area", arguments)
area = area_result.tables["area"]
area_metadata = area_result.metadata.outputs["area"]

result = runner.run("block_findings", arguments)
findings = result.tables["findings"]
inspection_points = result.tables["inspection_points"]
run_metadata = result.metadata
findings_metadata = run_metadata.outputs["findings"]

summary_result = runner.run("count_blocks", arguments)
summary = summary_result.tables["summary"]
summary_metadata = summary_result.metadata.outputs["summary"]
```

Inspect the run and output descriptions through ordinary attributes:

```python
area_result.metadata.metric_name       # "block_area"
area_result.metadata.parameters        # {} for this metric

output = area_result.metadata.outputs["area"]
output.scope                          # "location"
output.table_schema                   # Descriptive schema, including units
output.owner_scope                    # Owning provider scope for this run
output.owner_scope.provider_id        # "city_blocks"
output.owner_scope.code_revision
output.owner_scope.origins             # Declared origin identities

summary_metadata.scope                # "unpartitioned"
summary_metadata.owner_scope          # None
```

The area and findings descriptions identify their owning `city_blocks` provider
scope; the count summary has no owning provider scope despite consuming blocks.
`parameters` contains the root metric's effective values, including defaults and
nulls when declared. Each output retains its declared `table_schema` even when its
table is empty, and empty location-scoped outputs retain their `owner_scope`.
The three result objects retain their own tables and metadata after subsequent
runs. The native frames are usable directly with pandas and GeoPandas.

### Primary locations and complete reference inputs

The existing `block_area` metric has one primary input, `blocks`, and one output,
`area`. Use it twice to compare main blocks with an independent reference set.
`COMPARISON_SCHEMA` is a location-scoped output schema; its declaration and the
calculation body are omitted here:

```python
from lyra.sdk import Dependency, Input


@metric(
    name="compare_block_areas",
    description="Compare block areas with reference areas.",
    inputs={
        "blocks": LocationDependency(
            provider="city_blocks",
            primary=True,
        ),
        "reference_blocks": LocationDependency(
            provider="city_blocks",
        ),
        "areas": Dependency(
            producer="block_area",
            output="area",
        ),
        "reference_areas": Dependency(
            producer="block_area",
            output="area",
        ).bind(
            blocks=Input("reference_blocks"),
        ),
    },
    outputs={"comparison": COMPARISON_SCHEMA},
)
def compare_block_areas(
    blocks: gpd.GeoDataFrame,
    reference_blocks: gpd.GeoDataFrame,
    areas: pd.DataFrame,
    reference_areas: pd.DataFrame,
) -> pd.DataFrame:
    ...
```

`areas` connects the consumer's primary `blocks` to the producer's primary
`blocks` through matching-name forwarding. In the other dependency's `.bind()`
call, `blocks=` names the producer's argument and `Input("reference_blocks")`
references the caller's independent input. Both reference inputs retain their
complete selections. Output owners come from the input marked `primary=True`.
Omitting `.bind()` here makes both area dependencies use the caller's `blocks`;
that is a valid binding, regardless of the name `reference_areas`.

With the comparison metric registered on the runner:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["A", "B"]},
    "reference_blocks": {"provider": "city_blocks", "ids": ["X", "Y"]},
}
result = runner.run("compare_block_areas", arguments)
comparison = result.tables["comparison"]
```

With `per_location` caching in milestone 9 and a valid cached result for A, the
comparison handler receives:

| Input | Supplied records |
| --- | --- |
| `blocks` | B |
| `areas` | B's area |
| `reference_blocks` | X and Y |
| `reference_areas` | X's and Y's areas |

The upstream `block_area` uses still follow their own cache policies. An `off`
main-area computation, or a `full` computation on a miss, executes its original
A/B binding before B's rows are forwarded. Changing the complete reference context
changes the comparison's computation identity.

If the upstream metric instead names its location input `block_geometries`, both
area dependencies need an explicit binding. Keep the caller's location declarations
and handler arguments above; the two dependency entries become:

```python
{
    "areas": Dependency(
        producer="block_area",
        output="area",
    ).bind(
        block_geometries=Input("blocks"),
    ),
    "reference_areas": Dependency(
        producer="block_area",
        output="area",
    ).bind(
        block_geometries=Input("reference_blocks"),
    ),
}
```

The keyword on the left names the producer's location argument; the reference on
the right names the caller's input. There is no caller input named
`block_geometries`, so omitting either binding leaves a required producer input
unbound and fails plan validation before acquisition.

### Declaring an operation

Assume the `city_blocks` provider also declares a nullable string `county_id`
attribute identifying an existing location in `census_counties`. The operation
computes the association from supplied records and opts into automatic routing:

```python
from lyra.sdk import location_operation


@location_operation(
    name="county_for_blocks",
    description="Associate each block with its county.",
    inputs={
        "blocks": LocationDependency(provider="city_blocks"),
    },
    target_provider="census_counties",
    canonical=True,
)
def county_for_blocks(
    blocks: gpd.GeoDataFrame,
) -> dict[str, tuple[str, ...]]:
    return {
        block.location_id: (
            () if pd.isna(block.county_id) else (block.county_id,)
        )
        for block in blocks.itertuples(index=False)
    }
```

The sole location input is primary. An operation with several location inputs
marks exactly one with `primary=True`. The return structure is fixed, and target
record schemas come from the provider, so operations need no `outputs` schema.
A possible return value is:

```python
{
    "A": ("county_1",),
    "B": ("county_1",),
    "C": (),
}
```

A and B share a target; C completed with no match. Lyra resolves the returned IDs
through `census_counties` and forwards correspondence rows with county records.
The `county_id` attribute alone establishes no association or acquisition; the
operation's mapping declares the relationship. Missing target records fail
acquisition. The handler performs only computation on its supplied inputs.

A caller with a `selected_blocks` location input can reference the operation using:

```python
LocationDependency(
    operation="county_for_blocks",
).bind(
    blocks=Input("selected_blocks"),
)
```

[Milestone 7](07-operation-target-selection-with-ibis.md) adds optional
`target_selector=...` candidate discovery to this declaration while retaining the
same mapping return type and fixed target provider.

### Automatic routing and tie preferences

The `canonical=True` declaration above allows Lyra to route a `city_blocks`
selection to an input requiring `census_counties`. Omitting it leaves the operation
available for explicit invocation without adding an automatic route.

Assume `alternative_county_for_blocks` is another canonical operation between
those providers, and `county_statistics` is a metric with a `counties` input
requiring `census_counties`. Both providers are registered by installed plugins.
Resolve the two one-operation routes with a plugin preference:

```python
from lyra.sdk import PluginDefinition


plugin = PluginDefinition(
    metrics=[county_statistics],
    location_operations=[county_for_blocks, alternative_county_for_blocks],
    route_preferences=[(county_for_blocks,)],
)
```

Each tuple names a complete route; a route with several operations lists them in
traversal order. Without the preference, these two shortest routes are ambiguous.
A longer route cannot replace a shorter one through a preference.

With a runner configured for these plugins, supply source blocks under the metric's
named `counties` input:

```python
result = runner.run(
    "county_statistics",
    {"counties": {"provider": "city_blocks", "ids": ["A", "B", "C"]}},
)
```

Lyra invokes the preferred operation, resolves its county IDs, and supplies the
metric with county correspondence rows. The request names the receiving input and
source selection; its provider does not replace the metric's declared requirement.
Supplying `census_counties` IDs directly uses no operation. An explicitly bound
operation remains available even when it is not the preferred automatic route.

### References related through an operation

For a comparison against each block's own neighbours, assume a registered
`reference_blocks_for_blocks` operation with primary input `blocks` and source
and target provider `city_blocks`. Also assume a metric `compare_neighbour_blocks`
whose `blocks` and `reference_blocks` inputs both require `city_blocks`, with
`primary=True` on `blocks`. Its primary `blocks` input receives main records and
its `reference_blocks` input receives
correspondence rows. The handler calculates comparisons from those native frames,
grouping references by `source_location_id`.

A caller with a `blocks` input can bind that metric as follows:

```python
from lyra.sdk import LocationDependency


inputs = {
    "comparison": Dependency(
        producer="compare_neighbour_blocks",
        output="comparison",
    ).bind(
        blocks=Input("blocks"),
        reference_blocks=LocationDependency(
            operation="reference_blocks_for_blocks",
        ).bind(
            blocks=Input("blocks"),
        ),
    ),
}
```

Suppose the operation returns `{"A": ("X", "Y"), "B": ("Y", "Z")}`. Its
correspondences relate reference records to the comparison's primary blocks.
When only B needs calculation, its consumer receives B/Y and B/Z correspondence
rows. With A and B both needed, Y appears in both groups while its authoritative
provider record remains unique. The relationship comes from the operation mapping,
even though both roles use the same provider.

Each upstream operation or metric still follows its own cache policy: only
`per_location` permits reducing its computation to needed partitions. The
operation must describe the intended reference groups; a comparison against a
common complete reference set retains that context as in the preceding sketch.

## Completion boundary

Local, development, and remote execution can run the new contracts end to end,
including private development roots, operation chains, multiple inputs/outputs,
unpartitioned summaries, and hybrid graphs. Existing result paths are adapted to
these contracts and remain usable before the storage redesign.
Local and development calls return `RunResult` with named native tables and
metadata for that execution. Public construction, handler inputs, and individual
table access use native tables throughout.

Isolated behavioral checks cover invalid plans before acquisition, ownership and
named-output failures, shared targets, missing targets, explicit and omitted empty
operation groups, empty tables with schemas, unordered input permutations, and
whole-table forwarding. Each validation boundary has one owner without losing
mutation isolation or meaningful resource checks.

Schema checks cover both `DataFrameSchema` and `DataFrameModel`, including inherited
strict configuration. Reject non-strict and filtering schemas, optional or regex
columns, and automatic missing-column insertion before acquisition. Producer checks
reject extra, missing, and duplicate columns while accepting declared nullable
values and column permutations. Exercise automatic validation with handlers that
only return native tables. Cover per-location calculations on repeated targets in
operation-derived inputs: output owner/key pairs remain unique, and handlers using
the relationships still receive every distinct correspondence row.

Run separate concrete block and tract metrics using different fake providers.
Verify explicit and matching-name dependency forwarding, route selection and
ambiguity handling, failure when no required route exists, and actual output
ownership. Cover two uses of the same metric with different ID selections or
parameters in one graph, empty selections retaining scope, preservation of
registered columns, and provider schema/CRS failures. Changing a source selection
must leave declared operation targets and independent origins unchanged.

Verify that only canonical operations participate in automatic routing, while
explicit bindings can invoke either kind. Reject canonical operations with several
source location inputs or unsatisfied dependencies/parameters before acquisition.
Cover routes spanning plugins, direct zero-operation routes, and preferences for
single-operation and chained shortest routes. Reject unknown or noncanonical
references, disconnected or longer preferences, conflicting preferences, and
unresolved shortest-route ties independently of registration order.

Verify that explicit and matching-name bindings establish equivalent primary-input
relationships. Cover renamed producer arguments and missing required bindings.
Verify that metrics with zero, one, or multiple location inputs are unpartitioned
when none is marked, including for empty selections. One explicit primary marker
selects location ownership; reject multiple markers, markers on ordinary
metric-table dependencies, and nested attempts to override the receiving node's
primary input. Verify sole-source primary inference for operations and reject
operations without a location input or without exactly one primary marker when
several location inputs exist. Independent reference inputs remain complete even
when their providers or IDs overlap the primary input; a binding only through a
producer's non-primary input also remains complete. Exercise operation-derived reference
groups with overlapping targets and unpartitioned outputs retaining their complete
bound context. Invalid bindings, missing required inputs, and missing routes still
fail before acquisition.

Verify that changing input attributes, IDs, geometry, CRS, and frame metadata
cannot affect another named input, consumer, authoritative record, or retained
result. Retain results from consecutive local and development runs and verify that
each keeps its own tables, effective parameters, and output descriptions. Check
matching table/metadata output names and correct location or unpartitioned scopes,
including empty outputs. Verify the root `metric_name`, effective `parameters`
including defaults and nulls, and `table_schema` descriptions consistent with
discovery. Check that `owner_scope` retains the execution's provider ID, code
revision and origin identities for location-scoped outputs and is `None` for
unpartitioned outputs even when they consume locations.
Nested mutations to a returned result's metadata cannot
affect retained state or another result. Include producer-held and caller-held
references, original-selection ownership checks, and distinct schema rules for
unique records versus repeated
targets in correspondence rows. Cover empty and all-null native frames, declared
dtypes, and both local and worker execution. Measure representative execution
time and memory with existing benchmarks without pinning custom snapshot,
fingerprint, or buffer-sharing implementation details.

## Deferred or excluded work

[Managed target selection](07-operation-target-selection-with-ibis.md),
[storage](08-sqlite-result-storage.md), [persistent caching](09-node-caching.md),
and [automatic reports](10-execution-reports-and-durable-success.md) are subsequent
milestones. Their additions must preserve the input-selection, producer-scope,
identity, and completion rules defined here.

Managed requests do not accept full location-record injection, arbitrary
DataFrames, or caller-supplied stored-result references replacing dependencies.
Direct Python handler calls remain outside managed execution and reporting.

Caller-selectable operation targets, mixed-provider rows within one named location
input, and automatic temporal or spatial context substitution are outside this
implementation.

Operations map existing locations, including one-to-many and many-to-one
relationships. Creating, identifying, merging, splitting, or registering new
locations inside operations is outside scope; external preparation exposes them
through registered providers. Secondary metric associations, exact contributing-row
claims, and finding-level provenance are deferred.

## Reference points

Core declarations live in [node definitions](../src/lyra/sdk/plugin.py),
[output contracts](../src/lyra/contracts/outputs.py), and
[dependency bindings](../src/lyra/contracts/dependencies.py).
Execution uses the [route compiler](../src/lyra/runtime/planning/catalog.py),
[executor](../src/lyra/runtime/execution.py), and
[producer validation](../src/lyra/sdk/results.py).
The public-value replacement also spans [location values](../src/lyra/data/locations.py),
[standalone artifact inspection](../src/lyra/data/artifact_reader.py), and
[operation context](../src/lyra/sdk/context.py).
[Routing behavior tests](../tests/test_canonical_routes.py) describe existing route guarantees.
Pandera documents [DataFrame schemas](https://pandera.readthedocs.io/en/stable/dataframe_schemas.html).
