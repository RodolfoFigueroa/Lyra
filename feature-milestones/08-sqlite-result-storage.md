# 8. SQLite result storage

Store immutable values under their declared identities using one relational
result layer for local and API/worker execution. This supplies computation keys,
typed native rows, completion records, ordinary retrieval, and automatic persistent
local storage. Cache scheduling and final result/report publication follow later.

## Accepted scope

### Computation and location-record identities

Each executable node may declare an opaque `code_revision: str | None = None`;
implementation identity is `(node_id, code_revision)`. Supplied tokens must be
strict nonblank strings, preserved exactly without coercion or normalization.
Omission and explicit `None` have the same stable identity. Authors change the
token or node ID for output-affecting implementation or declaration changes.
Lyra compares tokens without interpreting versions, hashing code, inspecting
package versions, or detecting changes automatically.
Authors may automate token updates in their own tooling; Lyra requires only the
declared identity. Physical batching does not define another implementation or
stored-value version.

Use shared identity descriptors with an explicit kind discriminator:

| Value | Identity contents |
| --- | --- |
| Base provider record | Kind and immutable `(provider_id, code_revision, origin identities, location_id)` |
| Metric computation | Kind, implementation identity, output scope, computation scope, effective parameters, all named input identities, effective direct-origin IDs/revisions, and primary-input name when location scoped |
| Operation computation | Kind, implementation identity, location scope, computation scope, effective parameters, sole named source binding identity, and declared target provider scope; implementation identity covers selection and mapping |

Provider scope means `(provider_id, code_revision, origin identities)`, with origin
identities deduplicated and unordered. Resolve provider requirements and their
scopes before forming the applicable computation key. Named input identities
include the resolved provider scope even for empty selections. Distinguish uses
of one metric by their effective ID selections and parameters under the
computation-scope rules below. Changed provider code or origin revisions contribute
distinct input identities even when bare IDs match.

[Milestone 14](14-interventions.md) extends these descriptors for interventions.
Keep the base provider scope intact and include the applicable intervention-set
identity in effective location-input and target-dataset identities. Added record
keys include the base scope, set identity, and location ID; unchanged base records
retain their existing keys. Set and intervention order are insignificant. This
extension uses the same descriptor and storage machinery, without changing the
base identities or introducing a global run-specific invalidation token.

An optional target selector belongs to its operation's implementation identity.
The same `code_revision` covers output-affecting changes to either callback.
The sole source binding and all effective parameters contribute through the
ordinary operation descriptor. Target provider scope fixes base searchable
membership and record contents; with interventions, the effective target identity
also includes the set identity before selection executes. Candidate IDs and the
constructed Ibis expression are execution results, not prerequisites for constructing the key.
Do not introduce independent selector versions, expression hashes, query-cache
entries, or candidate-membership tables solely for selection.

A qualified partition combines provider scope and location ID for the distinct
represented locations in the node's selected primary input. Providers partition
by their own immutable records. In an operation-derived primary input, repeated
target `location_id` values belong to the same target partition while distinct
incoming source/target pairs remain visible. An operation's own output partitions
instead belong to its mapping keys, forwarded as `source_location_id`.

Under milestone 14, computation ownership and correspondence roles also retain
their effective dataset context. References to underlying records distinguish
base records from intervention-qualified additions even when they share one
effective input table. Unchanged base rows need no duplicate stored values for
each intervention set.

Full and uncached computations cover the complete selection initially bound for
the request. Per-location metrics cover one qualified primary partition, its
applicable inputs, and complete shared inputs. Per-location operations cover one
qualified partition of their sole source, retaining its upstream operation
provenance. Unpartitioned metrics cover all complete bound inputs without a primary
location or owning partition. Input names distinguish roles and participate in
identity even when several metric inputs refer to the same provider. Shared means
the complete selection bound to that input, not the provider's entire collection.

Derive output scope and the primary input from the node's declarations using
[milestone 6](06-unified-graph-execution.md), and retain them in stored metadata.
An operation's sole source is automatically primary. Use milestone 6's automatic
input rules for applicable partition groups and metrics' complete shared context.
Every retained shared metric input contributes its complete bound identity to
each dependent metric computation.
Changing a reference selection or its data identity therefore changes dependent
keys, even when the primary location is unchanged.

Provider inputs contribute their scope and selected IDs. Provider code revisions
and origin identities participate in both record keys and downstream computations.
Resource aliases, credentials, acquisition batches, cache settings, and selection
paths add no identity inputs. Output-affecting configuration must be represented
through declared provider or origin identities. A base provider scope fixes geometry,
attributes, and base membership, including for fixed unversioned origins. Reacquisition
with caching disabled cannot return different contents under the same record key.
No content-based verification establishes these identities. Predicate selection
uses the same record keys and typed tables as exact-ID resolution.

Metric-table dependencies reference compact upstream computation keys, the named
output, and selected groups where applicable. Location inputs, including an
operation's sole source, retain upstream operation keys and correspondence groups
when supplied through an operation. Per-location producers contribute
each selected partition's identity independently of execution batching; `off` and
`full` producers contribute their complete initially bound computation identity,
even when consumers select fewer output groups. Dependencies on unpartitioned
metrics reference whole tables. Operation correspondences used to select records
remain computation dependencies after target records are resolved. Resulting
target IDs are outputs, not prerequisites for the operation's own descriptor.

For example, a per-location computation for A has the same `K_A` whether A runs
alongside B or C, provided its effective inputs and shared context match. A full
or uncached A/B/C computation has `K_ABC`; a consumer selecting only its A group
still references `K_ABC`. A/B/D has a different complete-computation identity.
Upstream keys are compact references, not recursively embedded ancestor
descriptors. An operation's target provider scope is known before execution even
when its resulting target IDs are not.

Upstream implementation and origin changes propagate through these references.
An origin is an immutable `(id, revision)` descriptor for external data. Metrics
and providers declare direct origins on their own definitions; deduplicate within
each node before key construction, without initializing resources. Their order,
descriptions, and Python object identity do not affect keys. Provider scopes
already contain their declared origin identities. Deployment build revisions,
job/run IDs, storage references, resource names, credentials, connection settings,
and location/output content hashes add no identity inputs. Authors represent
output-affecting differences through declared identities or effective parameters.

Operations have no direct origins or resource bindings; the target provider scope
and sole source binding describe their data dependencies. That binding retains
upstream operation provenance without permitting metric-table dependencies or
additional location inputs. Managed target acquisition adds no separate origin
token to the operation's key. Callback table argument names do not replace the
declared source name in the descriptor.

Serialize Pydantic's complete effective parameter representation, including nulls
and defaults. Equal omitted and explicit defaults have equal identity. Preserve
scalar distinctions remaining after validation and sequence-valued parameter list
order. Arrange unordered identity collections consistently by explicit keys;
location order and dictionary insertion order are insignificant. Encode descriptors
as deterministic compact JSON with sorted object keys and non-finite numbers
rejected, then hash UTF-8 bytes with SHA-256 using standard libraries. Unsupported
values fail without string/repr fallback. Internal sorting promises no result order.

The supported parameter representation is JSON strings, booleans, integers,
finite floats, null, lists, string-keyed dictionaries, and nested models serialized
by Pydantic. Models cannot exclude fields or apply custom serializers. Their
ordinary validators must be deterministic, idempotent, and free of external I/O;
trust authors without extra behavioral proof runs. The API queues all effective
values and the worker revalidates them without independently choosing defaults.
No supplied/default-origin metadata contributes to identity.

For example, location selections A/B and B/A have equal identity. After ordinary
validation, string `"1"` and integer `1` remain distinct if their types remain
distinct; parameter lists `["X", "Y"]` and `["Y", "X"]` remain different.
Meaningful sequence in table data is explicit scalar data, not row position.
The kind discriminator separates record and computation key spaces without
another author-managed version or acquisition token.

The same computation key identifies the same logical values and membership,
including empty results, across executions and cache settings. An `off` node still
has a logical computation identity. Authors own determinism; Lyra does not prove
it with output hashes or comparisons.
Output-affecting time periods, source releases, code changes, and other differences
must enter through declared identities or effective key inputs, including when
caching is off. Different values under one key are a plugin-contract violation,
not a valid new stored version. An execution ID identifies an execution report
separately; equal values do not imply the same execution.

### Typed storage from Pandera schemas

Use SQLite native rows, SQLAlchemy Core schema construction and bulk statements,
and the once-compiled descriptions derived from registered Pandera schemas.
Registration performs no database access; initialization and writes occur during
runtime storage use. Authors supply no second SQL schema.
The declaration can be a Pandera `DataFrameSchema` or `DataFrameModel`; schema
helpers compile declarations without introducing a custom data container. Reject
unsupported types at registration. Apply milestone 6's `strict=True`, fixed required
column contract: producer-validated tables contain exactly the columns compiled
for storage, with unique column names, including for empty and all-null results.
Consume runtime-owned, producer-validated DataFrames and GeoDataFrames with their
matching descriptions so mutation or an
alternate write path cannot bypass required checks. Handler and caller working
frames are isolated from those private values. Provider and correspondence scopes
come from runtime bindings or stored descriptions, never from frame attributes.

| Logical column | Storage and reconstruction |
| --- | --- |
| String | SQLite `TEXT`; pandas string |
| Signed 64-bit integer | SQLite `INTEGER`, from `-2**63` through `2**63 - 1`; pandas nullable integer |
| Finite 64-bit float | SQLite `REAL`; pandas nullable float |
| Boolean | SQLAlchemy `Boolean` using integer 0/1; pandas nullable boolean |
| Geometry | Standard WKB in a `BLOB` cell; Shapely/GeoPandas reconstruction with declared CRS |

Missing values become SQL `NULL` subject to declared nullability and structural
non-nullness. Preserve geometry validity requirements. Use explicit declared
types for populated, all-null, and empty results rather than inferring from rows.
Do not preserve arbitrary dtype widths, backing arrays, or categorical encodings.
Temporal and categorical data use supported strings or numbers; nested objects
and lists are not table cells. Parameters retain their separate JSON-container
contract, and operation handlers retain dictionary-of-tuples returns.

Keep a small mapping to library types and conversions. Pandera owns custom checks
and enabled coercion at the producer boundary; SQL constraints own stored structure
and scoped relationships. Do not build a general serializer, Pandera-to-SQL check
compiler, or repeated writer-side producer validation.
Provider schemas declare non-null unique string `location_id` and active
`geometry` with expected CRS. Location-scoped metric schemas declare non-null
string `location_id` and `row_key` with composite uniqueness, and each owner must
have been supplied through the primary input. Spatial metric schemas declare
active `geometry`, its type, validity, nullability, and CRS. These fixed names
cannot be replaced by aliases or reused as ordinary attributes.

### Shared relational layout

Use two shared value-metadata tables and a typed data table per registered named
output or provider registration. `value_key` is the computation or record
key itself, without another storage identity or key-to-value mapping layer.

| Table or value kind | Required scope and relationships |
| --- | --- |
| `stored_results` | Header keyed by `value_key`, with producer `node_id`, derived `output_scope`, and owning provider scope when location scoped |
| `stored_partitions` | `(value_key, location_id)` references the result header |
| Location-scoped metric rows | `(value_key, location_id, row_key)` within each output table; reference the owning partition |
| Provider records | One header, partition, and resolved data row per immutable record key; provider ID identifies the producer and registered schema/table |
| Operation pairs | `(value_key, source_location_id, location_id)`; source references the owning partition; retain separate source/target provider scopes |
| Unpartitioned rows | Reference the computation directly, without owning provider scope or partition records; internal row addressing preserves every row, including equal-valued duplicates |

The listed keys are scoped primary keys. `stored_partitions.value_key` references
its header. A metric row references `(value_key, location_id)` in that partition
table; an operation row references it using `(value_key, source_location_id)`.
The header's owning provider scope qualifies those IDs, retaining provider ID,
code revision, and origin identities. Omitted code or origin revisions retain
their stable `None` values. Provider headers identify their provider; their schema
comes from its registration. Unpartitioned headers have no owning provider scope.
With interventions, headers and retrieval descriptions preserve the applicable
effective dataset identities, and record references retain the qualification
needed to resolve additions separately from unchanged base records. Retain both
immediate source and target contexts for operation pairs. The physical encoding
of scope metadata remains an implementation choice.

One metric's named-output table stores its computations under the declared output
schema. For location-scoped computations, retain the primary input's concrete
provider scope on the header and retrieval description, including for empty results.
Unpartitioned metrics retain whole-table scope without an owner.

Use the actual declared structural columns rather than duplicating owner or row
identity fields. Provider scope belongs on headers and output descriptions rather
than every value row. Internal unpartitioned row identifiers support storage and
pagination, imply no semantic identity or ordering, and establish no correspondence
across computations. Internal fields such as `value_key` do not enter author tables.
A computation key alone cannot identify a metric row: one computation can own
several partitions and several rows per output. Repeated author row keys in
different partitions or outputs remain distinct. Physical table naming, additional
query indexes, and SQL construction details remain implementation choices.

Retain logical schemas once with output definitions or provider registrations,
including types, nullability, descriptions, units, and CRS needed for current-store
retrieval. Do not repeat schema documents per result or row. Store operation pairs
separately from provider records; derive joined forwarding tables and their schema
without another permanent copy or duplicated authoritative records.
Pair storage has strict string source/target IDs and their scopes, not tuple-valued
SQL cells. The forwarding schema adds target geometry and attributes, preserving
types, descriptions, units, nullability, and CRS even when empty. Provider values
and operation mappings are validated before expansion; provider uniqueness and
cardinality checks are not rerun on repeated target rows.

### Publication, completion, and retention

Publish a result header, applicable partitions, and every named-output row together
in a transaction after successful validation. Header presence means a complete
stored value. Absence means no value; cache policy separately determines reuse.
No separate completion object, per-row flag, or `cache_available` flag is needed.
Metric returns must contain every declared named output, including explicit
schema-valid empty tables, and no undeclared output names. Successful operation
mappings normalize omitted supplied source keys to empty groups; unsupplied keys
and duplicate targets within one group are invalid. Failure or unprocessed work
never creates a completed empty value.

Partition records preserve completed empty outputs. A full or uncached
location-scoped computation over zero locations can have a header with no
partitions or rows. A zero-location per-location request has no partition keys.
An unpartitioned header covers all named tables, including empty ones, without a
synthetic location. Empty inputs can still produce nonempty unpartitioned outputs.

For example, a full result `K_ABD` has one header and A/B/D partition records.
If B has no rows in a named output, its partition record still establishes empty
completion. Under per-location computation, A, B, and D instead have distinct
keys and headers even if one handler call computes them together. These are
logical groups of typed rows, not blobs, and do not require one invocation per key.

Provider headers require their single resolved record. Empty needed ID selections
produce no exact-ID resolver invocation or stored record; unresolved IDs are errors,
not cached empty records. Batched acquisitions can publish multiple independent
record keys in one transaction. An executed predicate selection with no matches
produces no provider record headers;
its operation can still publish completed empty source groups after successful
mapping. Query execution and query membership have no separate stored-value kind.

Milestone 14's complete intervention validation is separate from stored-value
completion. A retained computation or individual record header does not establish
validity of all supplied interventions. Any reusable complete validation is bound
to the same base and set identities. Intervention removal does not delete base
records or prior results from this store.

Store one immutable value per key. Repeated successful persistence retains the
existing value rather than another version; it does not imply that a handler was
skipped. Assembly and final-result descriptions reference keys and selected groups
without copying their values. Distinct full selections keep distinct keys;
partitioned SQL storage does not authorize subset matching for a full computation.

Persist required final values and retrieval descriptions. Intermediate metrics,
provider records, and operation mappings are stored when caching or ordinary
retrieval requires them; otherwise use them in memory. Do not persist snapshots or
identity-only membership solely for future tracing. Stored values need no historical
producing-invocation link and remain independently owned from future reports.

[Milestone 12](12-retained-location-inputs.md) extends ordinary retrieval to the
root metric's named location inputs. It adds required retention of input records,
operation pairs, and reconstruction references, including for retained metric
computations later reused as roots. That guarantee begins in milestone 12 and uses
this store's shared records, separate pairs, immutable references, and indefinite
retention; it does not require another stored copy of each joined input table.

Retain all data actually written indefinitely, append-only and immutable. Changed
keys, changed cache policies, and expired jobs do not delete it. Disabling caching
does not remove retained values or authorize that node's own reuse. Local managed
runs, including development, use the same store as API/worker execution, with an
automatically initialized persistent default and a normally configured path.
Result access identifies the associated store so callers can reopen it through
the path configuration and read-only interfaces sketched below.
Authors do not create store tables manually. Later enabled caching may reuse
retained records or computations; disabling caching neither deletes them nor
forces their own reuse while disabled. Storage growth is an accepted cost of
indefinite retention.

### Integrity, equality, and performance

Replace the custom location snapshot and IPC persistence machinery with native
table processing and this relational store. Remove cached content fingerprints,
custom Arrow-buffer canonicalization, snapshot-reuse comparisons, and IPC round
trips used solely to establish immutable in-memory buffers. Adapt the shared metric
storage consumers together with location storage; public APIs remain native tables
throughout. Use library WKB and Arrow conversions at actual persistence
or transport boundaries, with explicit frame ownership for in-memory isolation.
Do not replace the removed machinery with another snapshot framework or serialized
table-blob store. No format-version dispatch, migration, or old artifact reader is
required.

Use scoped primary/foreign keys, construction invariants, and transactional writes
to protect relationships. Damaged required stored data produces integrity errors;
lookups do not rerun handlers or fabricate values. Public inputs and cache reads
retain their ordinary validation, without recursive integrity sweeps.
Malformed public references are validation errors and valid references without
targets are not found. Broken required relationships are integrity failures,
separate from both cases. Check relationships through ordinary construction,
persistence, and lookup rather than another report-validation subsystem.

Location-content equality aligns qualified IDs and column names before library
table comparison, including Arrow comparison where used in memory. Types, values,
geometry, scope, CRS, and explicit sequence fields matter; physical arrangement
does not. Remove custom Arrow-buffer normalization used solely for equality or
integrity, and remove location-content fingerprints from cache identity. Ordinary
byte checksums may protect actual download/export artifacts; they neither define
logical equality nor require serialized table blobs or whole-database hashes.
Use library alignment and comparison rather than custom normalization rules for
each scalar representation. Potentially slower repeated table comparisons are an
accepted tradeoff. Different byte arrangements can have different checksums while
representing logically equal locations; neither comparison nor checksum is needed
to establish the trusted location identity.

Compile descriptions once, insert in batches, retrieve requested partitions
together with indexed queries, and reconstruct with library conversions. Use
existing ASV benchmarks for representative integrated workloads: roughly 1–5
value columns and 100–10,000 metric rows, with location data ranging from a few
dozen polygons to thousands of houses. These are design workloads, not limits;
geometry complexity and rows per partition matter.

Exploratory single-process measurements support native SQL storage at this scale:

| Synthetic data | Write | Full read | Single-partition read |
| --- | --- | --- | --- |
| 10,000 rows, four scalar value columns plus identities | 39–41 ms | About 13 ms | 0.4–0.6 ms |
| 10,000 point geometries | About 49 ms | About 16 ms | Not reported |
| 50 polygons, 10,000 vertices each | About 26 ms | About 9 ms | Not reported |

These measurements include conversion and database commit on write, or dataframe
reconstruction on read. Reads were warm; metric execution, Pandera validation, and
integrated report processing were excluded. Arrow blobs were faster for bulk
operations with larger groups and slower for many tiny groups. These observations
establish feasibility, not performance guarantees, API limits, or test thresholds.
Verify the integrated implementation on representative workloads rather than
requiring it to reproduce these exploratory timings.

## API sketches

### Implementation revisions

Metric and operation decorators accept `code_revision`, matching provider class
declarations from [milestone 5](05-location-providers-and-origins.md). These
declaration fragments reuse `AreaOutputSchema` from
[milestone 6](06-unified-graph-execution.md) and `TreeParameters` and
`select_mature_trees` from [milestone 7](07-operation-target-selection-with-ibis.md).
Handler bodies are omitted to focus on revision syntax.

```python
import geopandas as gpd
import pandas as pd

from lyra.sdk import LocationDependency, location_operation, metric


@metric(
    name="block_area",
    code_revision="2",
    inputs={"blocks": LocationDependency(provider="city_blocks", primary=True)},
    outputs={"area": AreaOutputSchema},
)
def block_area(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    ...


@location_operation(
    name="mature_trees_near_blocks",
    code_revision="3",
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
    ...
```

Omitting `code_revision` is equivalent to `code_revision=None`. Tokens such as
`"2"` and `"02"` are distinct; integers and blank strings are invalid. An
operation's token covers output-affecting changes to both its selector and
mapping handler. Changing a provider's token changes its record scope and the
identities of dependent computations.

### Persistent-store configuration

Local execution automatically uses persistent storage:

```python
from pathlib import Path

from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
```

The default is `.lyra/results.sqlite3` under the working directory. Resolve the
path to an absolute path when constructing the runner. Omitting the option enables
this default. To choose a file explicitly:

```python
runner = LocalRunner(
    plugins=["lyra-city-example"],
    result_store_path=Path(".lyra/results.sqlite3"),
)
```

`LocalRunner.from_definitions()` accepts the same `result_store_path` keyword.
Resolve explicit relative local paths at runner construction too. Managed
execution creates the directory and initializes storage when needed; `run()` and
`run_development()` follow the same persistence rules.

Deployment configuration selects the shared SQLite file for the API and workers:

```toml
result_store_path = "/lyra_data/results.sqlite3"
```

### Retained result access

Extend milestone 6's `RunResult.metadata` with `store_path`, the resolved local
`Path`, and `result_ref`, an opaque string identifying the retained final-result
description within that store:

```python
result = runner.run(
    "block_area",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]}},
)

area = result.tables["area"]
store_path = result.metadata.store_path
result_ref = result.metadata.result_ref
```

Retain the path and reference together for later access. The referenced
description selects the stored computations and groups composing the result,
including results assembled from several partitions. The reference addresses
that description; it does not require a single computation key or duplicate
stored values. Its encoding remains an implementation choice.

Each successful managed execution receives a distinct result reference, including
development runs, empty results, and executions served entirely from cached values.
Within its store, the reference permanently identifies that execution's immutable
final-result description and associated metadata. Reopening it retrieves the same
description. Separate descriptions can reference the same stored computation keys
without duplicating table rows.

A result reference identifies retained output access. Milestone 10's execution ID
identifies an execution and its report, associated with that execution's
final-result description. Job-based remote methods continue accepting plain job
IDs under milestone 3's contract.

Reopen the result in another process using the retained path and reference:

```python
from lyra.runtime.results import ResultStore

with ResultStore.open(store_path) as store:
    restored = store.read_result(result_ref)
    area = store.read_table(result_ref, "area")
```

`ResultStore.open()` opens an existing store read-only. `read_result()` returns
the same `RunResult` structure with all named tables and retained metadata.
`read_table()` loads one named output and returns a native DataFrame or
GeoDataFrame. Retrieval needs no runner, plugin loading, provider acquisition, or
handler execution. Returned tables and metadata remain usable after the context
closes.

Repeated executions have distinct references even when their stored values match:

```python
selections = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]},
}
first = runner.run("block_area", inputs=selections)
second = runner.run("block_area", inputs=selections)

assert first.metadata.result_ref != second.metadata.result_ref

with ResultStore.open(first.metadata.store_path) as store:
    restored = store.read_result(first.metadata.result_ref)

assert restored.metadata.result_ref == first.metadata.result_ref
```

### Lookup failures

Expose domain lookup exceptions from `lyra.contracts.errors`, sharing the
`ResultAccessError` base:

| Exception | Condition |
| --- | --- |
| `InvalidResultReferenceError` | The supplied result reference is malformed. |
| `ResultNotFoundError` | A well-formed reference has no retained result, or the requested output name is undeclared. |
| `ResultIntegrityError` | The result exists, but required retained data or relationships are damaged. |

Each lookup exception exposes `result_ref` and `output_name`, with
`output_name=None` for whole-result reads. Callers can inspect these fields without
parsing messages, or catch `ResultAccessError` for all three categories. These
domain exceptions form the public lookup contract; chain underlying storage
exceptions when relevant.

```python
from lyra.contracts.errors import ResultNotFoundError

with ResultStore.open(store_path) as store:
    try:
        area = store.read_table(result_ref, "area")
    except ResultNotFoundError as error:
        print(error.result_ref, error.output_name)
```

Requesting `"areas"` from a result declaring only `"area"` raises
`ResultNotFoundError`. Requesting the declared `"area"` when required backing data
is missing raises `ResultIntegrityError`. A valid empty output succeeds normally.

Opening a nonexistent database through `ResultStore.open()` raises the standard
`FileNotFoundError` and leaves the filesystem unchanged. Opening an existing store
and looking up a well-formed reference with no retained result instead raises
`ResultNotFoundError`.

### Reconstructed tables and metadata

Using milestone 6's `block_findings` metric in the same store:

```python
findings_result = runner.run(
    "block_findings",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]}},
)
findings_ref = findings_result.metadata.result_ref

with ResultStore.open(store_path) as store:
    restored = store.read_result(findings_ref)
    points = restored.tables["inspection_points"]
    description = restored.metadata.outputs["inspection_points"]

points.crs                            # Declared EPSG:32614 CRS
description.scope                     # "location"
description.table_schema              # Declared types, nullability, units, and CRS
description.owner_scope.provider_id   # "city_blocks"
description.owner_scope.code_revision
description.owner_scope.origins
```

Empty outputs retain their schemas, owner scopes, and applicable geometry/CRS.
Unpartitioned outputs retain `scope="unpartitioned"` and `owner_scope=None`;
equal-valued rows remain separate rows. Scalar columns reconstruct with pandas
string, `Int64`, `Float64`, and `boolean` dtypes according to the retained schema,
including all-null and empty columns. Spatial outputs use active `geometry`.
No relative row order or particular string backing array is promised.

Each read returns independent values:

```python
with ResultStore.open(store_path) as store:
    edited = store.read_table(result_ref, "area")
    edited["area_m2"] = 0

    original = store.read_table(result_ref, "area")
```

`original` contains the retained areas. Mutating a returned frame or nested
metadata cannot alter stored values or subsequent reads.

### Computation identity behavior

For the same node and computation scope, compare effective inputs after ordinary
validation, holding other inputs fixed:

| Change | Computation identity |
| --- | --- |
| Select A/B instead of B/A | Same |
| Explicitly supply an omitted default | Same |
| Reorder a list-valued parameter whose validated order differs | Different |
| Change an effective parameter or implementation revision | Different |
| Change provider scope or complete shared-input identity | Different |

These outcomes describe computation keys, independently of final-result
references and execution IDs. Authors declare revisions, origins, and inputs;
the runtime constructs the descriptors and keys.

## Completion boundary

Current local and remote result paths persist and read the new values correctly,
including empty and all-null tables, repeated unpartitioned rows, provider records,
and operation pairs. Preserve milestone 6's local `RunResult`: `.tables` exposes
native frames, and `.metadata.outputs` describes their schemas and the scopes
used by that execution, including when values are read from storage. Any enabled
cache path uses the new identity and value layer; the complete reuse/scheduling
contract is delivered next.

Local results expose their resolved `metadata.store_path` and durable
`metadata.result_ref`. Verify automatic and explicit path configuration,
development execution, and reopening through `ResultStore.open()` without loading
plugins. Whole-result reads preserve the associated metadata; both reading methods
return native values usable after the store closes and expose the named lookup
exceptions and their fields. Cover undeclared output names, damaged required data,
valid empty outputs, and opening a nonexistent database without creating files or
directories.
Repeated executions receive distinct result references while reopening preserves
each execution's description and metadata, including empty and fully reused results.

Isolated behavioral verification covers descriptor equivalence and distinctions,
including selector changes through operation revisions and target-provider code or
origin revisions, unchanged record identity across acquisition paths, schema
reconstruction, scoped relationships, atomic publication failures, idempotent
persistence, retained values, and reopening local result access. Cover operation
descriptors with one source binding, changed effective parameters, and changed
upstream operation provenance; equal omitted and explicit defaults have equal
identity. Verify distinct keys and owning scopes for separate concrete block and
tract metrics, including equal bare IDs and empty
selections. Also cover a metric across changed provider code or origin revisions
and different selections or parameters; read its computations from the same
named-output table without mixing partitions or revisions. Cover equivalent
explicit and matching-name bindings, and changed dependent keys when complete
reference context changes with an unchanged primary location. Verify native frame
reconstruction for empty and all-null data and isolation from producer-held, handler-held, and
caller-held references. Service boundaries remain mocked or use in-process fakes. Representative
performance verification assesses execution time and memory through existing
benchmarks without timing gates or assertions about fingerprint or buffer-sharing
implementation details.

## Deferred or excluded work

[Milestone 9](09-node-caching.md) completes cache lookup and scheduling.
[Milestone 10](10-execution-reports-and-durable-success.md) adds retained reports and
transactional final result/report publication; this storage milestone alone does
not claim that reporting guarantee. New transport access follows in
[milestone 11](11-result-and-report-retrieval.md).

SQLite targets one machine and modest workloads. Multi-machine workers and
high-workload deployment are not planned. Arrow is a transport, not persisted
tables or blobs; there is no separate filesystem artifact store. Automatic cleanup,
retention counts, ownership deletion, and individual cache invalidation are
deferred. Closed alpha requires no migrations or legacy readers across incompatible
code/schema changes; an explicit development-store reset is acceptable.

## Reference points

Existing boundaries include [storage-schema preparation](../src/lyra/sdk/storage_schema.py),
[content fingerprints](../src/lyra/data/content_fingerprints.py),
[location equality](../src/lyra/data/locations.py), and
[IPC verification](../src/lyra/data/ipc_codec.py).
