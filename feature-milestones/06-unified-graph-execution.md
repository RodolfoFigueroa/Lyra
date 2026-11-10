# 6. Unified graph execution

Make one runtime execute location-scoped, unpartitioned, and hybrid graphs with
explicit table identities and dependency selection. Providers, metrics, and
location operations retain their own ordinary interfaces while sharing planning,
execution, resources, validation, and the result paths. Later storage, caching,
and reporting extend this runtime rather than introducing parallel executors.

## Accepted scope

### Managed requests and routing

Managed local and remote requests accept parameters and location selections of
registered datasets and requested IDs. Providers supply geometry and attributes;
declared upstream nodes supply table dependencies. External tabular acquisition
belongs to providers and metrics with declared origins; operations perform no
direct external I/O. Direct provider inputs contain location records;
operation-derived inputs contain correspondence rows joined to target records,
with the relevant dataset/revision metadata.

A registered location is an immutable record identified by
`(dataset_id, revision, location_id)`; `revision=None` is fixed unversioned data.
Provider exact-ID resolvers accept the requested dataset, revision, and strict
string IDs, and return one schema-valid record per resolved ID. They have no
arbitrary calculation parameters or upstream-table arguments. Missing requested
records fail acquisition.
An upstream operation can select records without becoming part of their identity.

`run()` selects public metrics. `run_development()` selects concrete metrics,
including private roots, before shared request preparation. Both share selection
normalization, parameter and plan validation, provider acquisition, dependency
resolution, resources, and result validation. Subsequent storage, caching, and
reporting use these same paths. Public discovery and remote root submission remain
restricted to public metrics; declared dependencies execute normally.

Preserve shortest-route discovery across installed plugins, explicit preferences
for ties, ambiguity errors, and distinct operation and dataset identities. Authors
do not enumerate conversion sequences. Output scope and later reporting do not
filter routes or change tie resolution. The executor validates the complete plan
before resources or handlers, including unknown named inputs/outputs and invalid
bindings; reporting adds no alternative eligibility classification.

Resolve explicit metric origins and resource-binding origins into a collection
deduplicated by `(id, revision)` before cache lookup or resource initialization.
Providers derive their origin from the requested dataset registration. Every
direct external acquisition must be declared, including in unpartitioned metrics
and with caching off. Resources retain their usual configuration and lifecycle;
origin metadata neither initializes them early nor creates separate instances.
Reject resource bindings and direct-origin declarations on operations. Their
computational contract is author-owned; plan validation does not inspect Python
code or enforce an I/O sandbox.

### Output scopes and identities

Each metric has one scope for all named outputs. A location-scoped metric has at
least one location input and partitions by a particular named input. Select the
sole location input automatically; require explicit primary-input selection when
several exist. Location operations follow the same primary-input rule. Providers
partition by their own records.

The fixed handler-visible structure is:

| Table | Required structural columns | Row identity within its table scope |
| --- | --- | --- |
| Provider records | `location_id`, `geometry` | Unique `location_id` |
| Operation-derived location input | `source_location_id`, `location_id`, `geometry` | Unique source/target pair |
| Location-scoped metric output | `location_id`, `row_key`; `geometry` when spatial | Unique owner/row-key pair |
| Unpartitioned metric output | `geometry` when spatial; no mandatory identity columns | No author row identity required |

Location IDs and metric row keys are non-null strings. Dataset/revision metadata
qualifies each location-ID role; operation inputs carry separate source and target
scopes. A metric row key is additionally scoped to its named output. Consumers
address rows through the named input and those qualified identities, preserving
them across execution and later cache assembly without historical invocation links.

Every location-scoped metric output declares non-null string `location_id` and
`row_key` in Pandera with composite uniqueness. Its owner must be a distinct
represented `location_id` actually supplied through the primary input, qualified
by that input's dataset and revision. Multiple rows may share an owner, and an
owner may produce zero rows. The same row key may appear under different owners
or named outputs. Keys are stable across input permutations and permitted batching;
indexes and row positions are not identities. Authors encode numeric or composite
row identifiers as stable, unambiguous strings.

For example, `(A, "roof")`, `(A, "entrance")`, and `(B, "roof")` are distinct
rows in one findings output; two `(A, "roof")` rows there are invalid. If a
successful invocation receives A/B and returns only B's roof finding, A completed
with no rows. Ownership refers to the distinct locations actually supplied to that
primary input, not other locations elsewhere in the dataset or request. Check it
without another provider lookup. Different named inputs can use the same dataset
while serving different roles, so dataset identity alone cannot select a primary.

An operation-derived primary input partitions by its represented target
`location_id`, retaining distinct incoming correspondence rows. Its
`source_location_id` does not change the downstream owner's scope. Nonstructural
columns, including domain-named IDs of other locations, remain ordinary data;
they create no associations, acquisitions, or dependency edges.

Explicit unpartitioned scope bypasses primary-input inference and permits metrics
with no location inputs. Outputs are ordinary named tables without mandatory
owners or author row keys. Each invocation receives its complete bound selections,
which may be subsets of datasets. All outputs use whole-computation completion.
Mixed output scopes within one metric are unsupported; separate metrics provide
that composition. Unpartitioned metrics permit only `off` or `full` caching;
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

Operations return `dict[str, tuple[str, ...]]` directly. Keys belong to the supplied
primary input; each tuple contains unique target IDs from the declared target
dataset/revision. Repeated targets under different keys are valid. Strict Pydantic
validation owns mapping types; JSON objects with target arrays restore through
the declared type. Authors supply no additional output table, DataFrame schema,
group-dependency declarations, lineage payload, or reporting wrapper. Ordinary
named input bindings still declare the operation's dependencies.
Authors supply the mappings; Lyra does not infer correspondences from equal IDs
or geometry. Operations may perform spatial calculations internally to determine
which existing locations correspond, without creating or registering new locations.

[Milestone 7](07-operation-target-selection-with-ibis.md) extends this path with an
optional target selector. Lyra supplies the selector's source/dependency inputs and
a symbolic target table, executes its lazy filter through a managed dataset
adapter, and supplies validated candidates before invoking the mapping handler.
Candidates are context, not an additional primary-input choice. Both callbacks
remain computational. Operations deriving IDs directly require no selector.

A successful return covers all supplied keys. Omitted keys and empty tuples both
mean completed zero-match groups. Unsupplied keys and duplicates within a tuple
are errors. Targets must resolve at the dataset acquisition boundary; unresolved
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

Each consumer input has partition-related or shared selection behavior,
independent of cache policy. Partition-related inputs supply rows associated with
the requested primary keys through a declared correspondence. Shared inputs
retain their complete bound selection, not the entire external dataset.

Infer partition following for the primary input, dependencies explicitly
forwarding it into their own partitioning input, and operations deriving targets
through declared mappings and chains. Other unclear relationships require an
explicit shared binding or correspondence. Matching names, types, or IDs is
insufficient. An explicit shared binding may override partition following.
Represent this as an optional selection setting on existing input declarations,
not a separate dependency system. Labels such as `selection="partition"` and
`selection="shared"` illustrate the distinction; their spelling remains open.

Unpartitioned outputs are automatically shared whole-table dependencies. Reject
partition-selection bindings on them; column names and equal IDs cannot authorize
filtering. Hybrid graphs preserve the complete initially bound context of an
unpartitioned metric, even when downstream work later narrows through caching.
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
reduced records needed under either cache setting, while shared inputs remain complete. These
rules constrain the graph now; persistent reuse scheduling is implemented later.

Selections, target groups, partition groups, table rows, dictionary iteration,
and column positions carry no semantic ordering guarantee. Associate records by
qualified IDs and row keys and address columns by name. Handlers produce the same
logical result under input permutations. Meaningful sequence is explicit data,
such as rank or timestamp text, or ordered parameter values. Internal sorting
does not establish a public ordering promise.

Ownership, correspondence, and selection bindings preserve the relationships
needed for execution. They do not identify the exact rows that contributed to a
particular value or create a finding-level explanation contract.

### Validation and completion

Providers and metrics declare structural and ordinary columns together in Pandera
`DataFrameSchema` or `DataFrameModel`, including the existing location-schema
wrapper. Registration rejects missing or weakened structural constraints,
unsupported column types, and reserved-name collisions before execution. Spatial
schemas declare geometry type, nullability, validity, and CRS. Authors may add
compatible constraints.
Provider schemas require unique non-null string `location_id` and the expected
active geometry/CRS. Location-scoped metric schemas require non-null string
`location_id` and `row_key` with composite uniqueness. Derived operation schemas
require non-null source and target IDs with pair uniqueness while permitting
repeated targets. Unpartitioned metrics still require ordinary named-output,
schema, and supported-type validation.

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

One shared producer boundary validates each result, including Pandera checks and
explicitly enabled coercion, mapping normalization, and invocation membership.
Metrics return every declared named output and no additional output names, with
explicit schema-valid empty tables. Missing a metric output is not empty
completion. Column acceptance follows its schema. Writers consume owned validated
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
All executable nodes operate deterministically over frozen inputs, including when
caching is disabled. Output-affecting changes must be represented in declared
implementation/data identities or effective parameters. A structurally valid row
can still have an unintended supplied owner; Lyra cannot infer that semantic error
or inspect handler code to prove complete source declarations.

Decorator and result-class names, wrapper/accessor names, and output-scope setting
spelling remain implementation choices. Structural column names and the
validation, scope, and ordinary return contracts above are fixed; no reporting
flag changes handler returns.

## Completion boundary

Local, development, and remote execution can run the new contracts end to end,
including private development roots, operation chains, multiple inputs/outputs,
unpartitioned summaries, and hybrid graphs. Existing result paths are adapted to
these contracts and remain usable before the storage redesign.

Isolated behavioral checks cover invalid plans before acquisition, ownership and
named-output failures, shared targets, missing targets, explicit and omitted empty
operation groups, empty tables with schemas, unordered input permutations, and
whole-table forwarding. Each validation boundary has one owner without losing
mutation isolation or meaningful resource checks.

## Deferred or excluded work

[Managed target selection](07-operation-target-selection-with-ibis.md),
[storage](08-sqlite-result-storage.md), [persistent caching](09-node-caching.md),
and [automatic reports](10-execution-reports-and-durable-success.md) are subsequent
milestones. Their additions must preserve the input-selection, producer-scope,
identity, and completion rules defined here.

Managed requests do not accept full location-record injection, arbitrary
DataFrames, or caller-supplied stored-result references replacing dependencies.
Direct Python handler calls remain outside managed execution and reporting.

Operations map existing locations, including one-to-many and many-to-one
relationships. Creating, identifying, merging, splitting, or registering new
locations inside operations is outside scope; external preparation exposes them
as registered datasets. Secondary metric associations, exact contributing-row
claims, and finding-level provenance are deferred.

## Reference points

Core declarations live in [node definitions](../src/lyra/sdk/plugin.py),
[output contracts](../src/lyra/contracts/outputs.py), and
[dependency bindings](../src/lyra/contracts/dependencies.py).
Execution uses the [route compiler](../src/lyra/runtime/planning/catalog.py),
[executor](../src/lyra/runtime/execution.py), and
[producer validation](../src/lyra/sdk/results.py).
[Routing behavior tests](../tests/test_canonical_routes.py) describe existing route guarantees.
