# 6. Unified graph execution

Make one runtime execute location-scoped, unpartitioned, and hybrid graphs with
explicit table identities and dependency selection. This milestone establishes
the execution contracts in decisions 1–6, 10, 18, and 24 and R1/R3/R6/R7/R9 of
[the ledger](../DECISIONS.md). Storage, persistent reuse, and reporting build on
these contracts in the following milestones.

## Accepted scope

### Managed requests and routing

Managed local and remote requests accept parameters and location selections of
registered datasets and requested IDs. Providers supply geometry and attributes;
declared upstream nodes supply table dependencies. External tabular acquisition
occurs inside nodes with declared origins. Direct provider inputs contain location
records; operation-derived inputs contain correspondence rows joined to target
records, with the relevant dataset/revision metadata.

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

### Output scopes and identities

Each metric has one scope for all named outputs. A location-scoped metric has at
least one location input and partitions by a particular named input. Select the
sole location input automatically; require explicit primary-input selection when
several exist. Location operations follow the same primary-input rule. Providers
partition by their own records.

Every location-scoped metric output declares non-null string `location_id` and
`row_key` in Pandera with composite uniqueness. Its owner must be a distinct
represented `location_id` actually supplied through the primary input, qualified
by that input's dataset and revision. Multiple rows may share an owner, and an
owner may produce zero rows. The same row key may appear under different owners
or named outputs. Keys are stable across input permutations and permitted batching;
indexes and row positions are not identities. Authors encode numeric or composite
row identifiers as stable, unambiguous strings.

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
acquisition. The complete reuse behavior is delivered in milestone 8.

Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for their
structural roles. Every spatial table uses active `geometry`; nonspatial outputs
need none. Scopes belong to named input/output metadata, not repeated fields in
every row. Authors rename source fields before return; reject collisions and
aliases in place of required columns without automatic suffixing or overwrites.

### Operation correspondences and forwarding

Operations return `dict[str, tuple[str, ...]]` directly. Keys belong to the supplied
primary input; each tuple contains unique target IDs from the declared target
dataset/revision. Repeated targets under different keys are valid. Strict Pydantic
validation owns mapping types; JSON objects with target arrays restore through
the declared type. Authors supply no additional output table, DataFrame schema,
lineage payload, or reporting wrapper.

A successful return covers all supplied keys. Omitted keys and empty tuples both
mean completed zero-match groups. Unsupplied keys and duplicates within a tuple
are errors. Targets must resolve through their provider; unresolved IDs fail
ordinary acquisition rather than disappearing or becoming zero matches. No
separate preflight existence lookup is required.

Resolve each distinct needed target once and join by explicit IDs. Forward one
row per `(source_location_id, location_id)` pair, with the target's geometry and
attributes. A shared target appears under every source selecting it; authoritative
provider records remain unique. Zero-match groups produce no forwarded rows or
fabricated geometry, but retain completion where execution requires it.

Lyra derives forwarding schemas from the target provider schema and source/target
scopes. Preserve attribute types, nullability, descriptions, units, and target CRS,
including for empty tables. Add non-null string source IDs and pair uniqueness.
Provider uniqueness and cardinality checks apply before expansion, not unchanged
to repeated target rows. Reuse validated values and enforce construction invariants
without translating arbitrary Pandera checks or revalidating provider tables.

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

Unpartitioned outputs are automatically shared whole-table dependencies. Reject
partition-selection bindings on them; column names and equal IDs cannot authorize
filtering. Hybrid graphs preserve the complete initially bound context of an
unpartitioned metric, even when downstream work later narrows through caching.

Selections, target groups, partition groups, table rows, dictionary iteration,
and column positions carry no semantic ordering guarantee. Associate records by
qualified IDs and row keys and address columns by name. Handlers produce the same
logical result under input permutations. Meaningful sequence is explicit data,
such as rank or timestamp text, or ordered parameter values. Internal sorting
does not establish a public ordering promise.

### Validation and completion

Providers and metrics declare structural and ordinary columns together in Pandera
`DataFrameSchema` or `DataFrameModel`, including the existing location-schema
wrapper. Registration rejects missing or weakened structural constraints,
unsupported column types, and reserved-name collisions before execution. Spatial
schemas declare geometry type, nullability, validity, and CRS. Authors may add
compatible constraints.

Compile a storage description once from each registered schema, without database
access or a second author-provided SQL schema. Supported columns are strings,
signed 64-bit integers, finite 64-bit floats, booleans, and geometry, with declared
nullability. Temporal and categorical values use supported strings or numbers;
nested objects and lists are not table-column types. The persistent representation
is defined in [milestone 7](07-sqlite-result-storage.md).

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
success. Empty needed provider selections invoke no handler. Zero-location work
does not fabricate rows, partitions, or invocations. Needed unpartitioned metrics
execute on empty inputs unless valid full reuse supplies the result: a summary
may legitimately return a nonempty count-of-zero table.

Authors own semantic correctness, intended ownership, determinism, stable keys,
complete origins/dependencies, and the independent-computation promise required
by per-location caching. Structural checks do not prove those properties.

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

[Storage](07-sqlite-result-storage.md), [persistent caching](08-node-caching.md),
and [automatic reports](09-execution-reports-and-durable-success.md) are subsequent
milestones. Input selection alone does not authorize reduced upstream metric or
operation execution; milestone 8 supplies the cache-driven scope rules.

Managed requests do not accept full location-record injection, arbitrary
DataFrames, or caller-supplied stored-result references replacing dependencies.
Direct Python handler calls remain outside managed execution and reporting.

Operations map existing locations, including one-to-many and many-to-one
relationships. Creating, identifying, merging, splitting, or registering new
locations inside operations is outside scope; external preparation exposes them
as registered datasets. Secondary metric associations, exact contributing-row
claims, and finding-level provenance are deferred.
