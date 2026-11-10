# 7. SQLite result storage

Store immutable values under their declared identities using one relational
result layer for local and API/worker execution. This supplies computation keys,
typed native rows, completion records, ordinary retrieval, and automatic persistent
local storage. Cache scheduling and final result/report publication follow later.

## Accepted scope

### Computation and location-record identities

Each executable node may declare an opaque `code_revision`; implementation identity
is `(node_id, code_revision)`, with `None` a stable omitted value. Authors change
the token or node ID for output-affecting implementation or declaration changes.
Lyra compares tokens without interpreting versions, hashing code, inspecting
package versions, or detecting changes automatically.
Authors may automate token updates in their own tooling; Lyra requires only the
declared identity. Physical batching does not define another implementation or
stored-value version.

Use shared identity descriptors with an explicit kind discriminator:

| Value | Identity contents |
| --- | --- |
| Provider record | Kind and immutable `(dataset_id, revision, location_id)` only |
| Metric computation | Kind, implementation identity, output scope, computation scope, effective parameters, all named input identities, effective direct-origin IDs/revisions, and primary-input name when location scoped |
| Operation computation | Metric components plus declared target dataset ID/revision |

A qualified partition is `(dataset_id, revision, location_id)` for the distinct
represented locations in the node's selected primary input. Providers partition
by their own immutable records. In an operation-derived primary input, repeated
target `location_id` values belong to the same target partition while distinct
incoming source/target pairs remain visible. An operation's own output partitions
instead belong to its mapping keys, forwarded as `source_location_id`.

Full and uncached computations cover the complete selection initially bound for
the request. Per-location computations cover one qualified primary partition,
its applicable inputs, and complete shared inputs. Unpartitioned computations
cover all complete bound inputs without a primary location or owning partition.
Input names distinguish roles and participate in identity even when several
inputs refer to the same dataset. Shared means the complete selection bound to
that input, not the entire external dataset.

Provider inputs contribute dataset/revision and selected IDs, independently of
provider node ID, code revision, acquisition batch, resource configuration, cache
setting, or the graph path selecting them. Record provider implementation later
in reports; changes to it do not invalidate immutable records or dependents.
The location tuple identifies frozen geometry and attributes even when revision
is `None`. Reacquisition with caching disabled cannot return different contents
under that tuple. Changed data requires a different dataset ID, revision, or
location ID; no content-based verification establishes this identity.

Metric and operation inputs reference compact upstream computation keys, the named
output, and selected groups where applicable. Per-location producers contribute
each selected partition's identity independently of execution batching; `off` and
`full` producers contribute their complete initially bound computation identity,
even when consumers select fewer output groups. Unpartitioned inputs reference
whole tables. Operation correspondences used to select records remain computation
dependencies after target records are resolved. Resulting target IDs are outputs,
not prerequisites for the operation's own descriptor.

For example, a per-location computation for A has the same `K_A` whether A runs
alongside B or C, provided its effective inputs and shared context match. A full
or uncached A/B/C computation has `K_ABC`; a consumer selecting only its A group
still references `K_ABC`. A/B/D has a different complete-computation identity.
Upstream keys are compact references, not recursively embedded ancestor
descriptors. An operation's target dataset is known before execution even when
its resulting target IDs are not.

Upstream metric/operation implementation and origin changes propagate through
these references. Origins are deduplicated independently of declaration placement;
their descriptions do not affect keys. Provider dataset identity already
represents its origin. Deployment build revisions, job/run IDs, storage references,
and location/output content hashes are excluded. An origin is an immutable
`(id, revision)` descriptor for direct external data. Combine explicit node
origins with those on resource bindings and deduplicate within the consuming node
before key construction, without initializing resources. Declaration placement,
Python object identity, resource names, credentials, and connection settings add
no identity inputs. Authors represent any output-affecting differences through
declared identities or effective parameters.

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
The declaration can be a Pandera `DataFrameSchema` or `DataFrameModel`, including
the location-schema wrapper. Reject unsupported types at registration. Consume
owned producer-validated data with its matching description so mutation or an
alternate write path cannot bypass required checks.

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
output or provider dataset registration. `value_key` is the computation or record
key itself, without another storage identity or key-to-value mapping layer.

| Table or value kind | Required scope and relationships |
| --- | --- |
| `stored_results` | Header keyed by `value_key`, with `node_id` for computations, explicit `output_scope`, and owning `partition_dataset`/`partition_revision` when location scoped |
| `stored_partitions` | `(value_key, location_id)` references the result header |
| Location-scoped metric rows | `(value_key, location_id, row_key)` within each output table; reference the owning partition |
| Provider records | One header, partition, and resolved data row per immutable record key; no provider `node_id` on the header; dataset registration identifies the schema/table |
| Operation pairs | `(value_key, source_location_id, location_id)`; source references the owning partition; retain separate source/target dataset scopes |
| Unpartitioned rows | Reference the computation directly, without owning dataset or partition records; internal row addressing preserves every row, including equal-valued duplicates |

The listed keys are scoped primary keys. `stored_partitions.value_key` references
its header. A metric row references `(value_key, location_id)` in that partition
table; an operation row references it using `(value_key, source_location_id)`.
The header's owning dataset/revision qualifies those IDs. `partition_revision`
may be null for fixed unversioned data and is not an additional author-managed
version. Provider headers omit `node_id`; their schema comes from the dataset
registration. Unpartitioned headers have no owning dataset or revision.

Use the actual declared structural columns rather than duplicating owner or row
identity fields. Dataset scope belongs on headers and output descriptions rather
than every value row. Internal unpartitioned row identifiers support storage and
pagination, imply no semantic identity or ordering, and establish no correspondence
across computations. Internal fields such as `value_key` do not enter author tables.
A computation key alone cannot identify a metric row: one computation can own
several partitions and several rows per output. Repeated author row keys in
different partitions or outputs remain distinct. Physical table naming, additional
query indexes, and SQL construction details remain implementation choices.

Retain logical schemas once with output definitions or dataset registrations,
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

Provider headers require their single resolved record. Empty needed selections
produce no provider invocation or stored record; unresolved IDs are errors, not
cached empty records. Batched acquisitions can publish multiple independent record
keys in one transaction.

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

Retain all data actually written indefinitely, append-only and immutable. Changed
keys, changed cache policies, and expired jobs do not delete it. Disabling caching
does not remove retained values or authorize that node's own reuse. Local managed
runs, including development, use the same store as API/worker execution, with an
automatically initialized persistent default and a normally configured path.
Result access identifies the associated store so callers can reopen it.
Authors do not create store tables manually. Default paths and configuration
spelling remain implementation choices. Later enabled caching may reuse retained
records or computations; disabling caching neither deletes them nor forces their
own reuse while disabled. Storage growth is an accepted cost of indefinite retention.

### Integrity, equality, and performance

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

## Completion boundary

Current local and remote result paths persist and read the new values correctly,
including empty and all-null tables, repeated unpartitioned rows, provider records,
and operation pairs. Any enabled cache path uses the new identity and value layer;
the complete reuse/scheduling contract is delivered next.

Isolated behavioral verification covers descriptor equivalence and distinctions,
schema reconstruction, scoped relationships, atomic publication failures,
idempotent persistence, retained values, and reopening local result access. Service
boundaries remain mocked or use in-process fakes. Representative performance
verification assesses the integrated implementation without inventing timing gates.

## Deferred or excluded work

[Milestone 8](08-node-caching.md) completes cache lookup and scheduling.
[Milestone 9](09-execution-reports-and-durable-success.md) adds retained reports and
transactional final result/report publication; this storage milestone alone does
not claim that reporting guarantee. New transport access follows in
[milestone 10](10-result-and-report-retrieval.md).

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
