# 7. SQLite result storage

Store immutable values under their declared identities using one relational
result layer for local and API/worker execution. This milestone covers the
identity, schema, storage, integrity, and retention decisions in items 6, 9, 11,
12, and 15 and R1–R4/R7 of [the ledger](../DECISIONS.md), plus R10's automatic
local-store initialization. Cache scheduling and report publication follow later.

## Accepted scope

### Computation and location-record identities

Each executable node may declare an opaque `code_revision`; implementation identity
is `(node_id, code_revision)`, with `None` a stable omitted value. Authors change
the token or node ID for output-affecting implementation or declaration changes.
Lyra compares tokens without interpreting versions, hashing code, inspecting
package versions, or detecting changes automatically.

Use shared identity descriptors with an explicit kind discriminator:

| Value | Identity contents |
| --- | --- |
| Provider record | Kind and immutable `(dataset_id, revision, location_id)` only |
| Metric computation | Kind, implementation identity, output scope, computation scope, effective parameters, all named input identities, effective direct-origin IDs/revisions, and primary-input name when location scoped |
| Operation computation | Metric components plus declared target dataset ID/revision |

Full and uncached computations cover the complete selection initially bound for
the request. Per-location computations cover one qualified primary partition,
its applicable inputs, and complete shared inputs. Unpartitioned computations
cover all complete bound inputs without a primary location or owning partition.

Provider inputs contribute dataset/revision and selected IDs, independently of
provider node ID, code revision, acquisition batch, resource configuration, cache
setting, or the graph path selecting them. Record provider implementation later
in reports; changes to it do not invalidate immutable records or dependents.

Metric and operation inputs reference compact upstream computation keys, the named
output, and selected groups where applicable. Per-location producers contribute
each selected partition's identity independently of execution batching; `off` and
`full` producers contribute their complete initially bound computation identity,
even when consumers select fewer output groups. Unpartitioned inputs reference
whole tables. Operation correspondences used to select records remain computation
dependencies after target records are resolved. Resulting target IDs are outputs,
not prerequisites for the operation's own descriptor.

Upstream metric/operation implementation and origin changes propagate through
these references. Origins are deduplicated independently of declaration placement;
their descriptions do not affect keys. Provider dataset identity already
represents its origin. Deployment build revisions, job/run IDs, storage references,
and location/output content hashes are excluded.

Serialize Pydantic's complete effective parameter representation, including nulls
and defaults. Equal omitted and explicit defaults have equal identity. Preserve
scalar distinctions remaining after validation and sequence-valued parameter list
order. Arrange unordered identity collections consistently by explicit keys;
location order and dictionary insertion order are insignificant. Encode descriptors
as deterministic compact JSON with sorted object keys and non-finite numbers
rejected, then hash UTF-8 bytes with SHA-256 using standard libraries. Unsupported
values fail without string/repr fallback. Internal sorting promises no result order.

The same computation key identifies the same logical values and membership,
including empty results, across executions and cache settings. An `off` node still
has a logical computation identity. Authors own determinism; Lyra does not prove
it with output hashes or comparisons.

### Typed storage from Pandera schemas

Use SQLite native rows, SQLAlchemy Core schema construction and bulk statements,
and the once-compiled descriptions derived from registered Pandera schemas.
Registration performs no database access; initialization and writes occur during
runtime storage use. Authors supply no second SQL schema.

| Logical column | Storage and reconstruction |
| --- | --- |
| String | SQLite `TEXT`; pandas string |
| Signed 64-bit integer | SQLite `INTEGER`, within signed 64-bit range; pandas nullable integer |
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

### Shared relational layout

Use two shared value-metadata tables and a typed data table per registered named
output or provider dataset registration. `value_key` is the computation or record
key itself, without another storage identity or key-to-value mapping layer.

| Table or value kind | Required scope and relationships |
| --- | --- |
| `stored_results` | Header keyed by `value_key`, with `node_id` for computations, explicit output scope, and owning dataset/revision when location scoped |
| `stored_partitions` | `(value_key, location_id)` references the result header |
| Location-scoped metric rows | `(value_key, location_id, row_key)` within each output table; reference the owning partition |
| Provider records | One header, partition, and resolved data row per immutable record key; no provider `node_id` on the header; dataset registration identifies the schema/table |
| Operation pairs | `(value_key, source_location_id, location_id)`; source references the owning partition; retain separate source/target dataset scopes |
| Unpartitioned rows | Reference the computation directly, without owning dataset or partition records; internal row addressing preserves every row, including equal-valued duplicates |

Use the actual declared structural columns rather than duplicating owner or row
identity fields. Dataset scope belongs on headers and output descriptions rather
than every value row. Internal unpartitioned row identifiers support storage and
pagination, imply no semantic identity or ordering, and establish no correspondence
across computations. Internal fields such as `value_key` do not enter author tables.

Retain logical schemas once with output definitions or dataset registrations,
including types, nullability, descriptions, units, and CRS needed for current-store
retrieval. Do not repeat schema documents per result or row. Store operation pairs
separately from provider records; derive joined forwarding tables and their schema
without another permanent copy or duplicated authoritative records.

### Publication, completion, and retention

Publish a result header, applicable partitions, and every named-output row together
in a transaction after successful validation. Header presence means a complete
stored value. Absence means no value; cache policy separately determines reuse.
No separate completion object, per-row flag, or `cache_available` flag is needed.

Partition records preserve completed empty outputs. A full or uncached
location-scoped computation over zero locations can have a header with no
partitions or rows. A zero-location per-location request has no partition keys.
An unpartitioned header covers all named tables, including empty ones, without a
synthetic location. Empty inputs can still produce nonempty unpartitioned outputs.

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

### Integrity, equality, and performance

Use scoped primary/foreign keys, construction invariants, and transactional writes
to protect relationships. Damaged required stored data produces integrity errors;
lookups do not rerun handlers or fabricate values. Public inputs and cache reads
retain their ordinary validation, without recursive integrity sweeps.

Location-content equality aligns qualified IDs and column names before library
table comparison, including Arrow comparison where used in memory. Types, values,
geometry, scope, CRS, and explicit sequence fields matter; physical arrangement
does not. Remove custom Arrow-buffer normalization used solely for equality or
integrity, and remove location-content fingerprints from cache identity. Ordinary
byte checksums may protect actual download/export artifacts; they neither define
logical equality nor require serialized table blobs or whole-database hashes.

Compile descriptions once, insert in batches, retrieve requested partitions
together with indexed queries, and reconstruct with library conversions. Use
existing ASV measurements for representative integrated workloads: roughly 1–5
value columns and 100–10,000 metric rows, with location data ranging from a few
dozen polygons to thousands of houses. These are design workloads, not limits;
geometry complexity and rows per partition matter. The ledger's exploratory
timings establish feasibility, not guarantees or test thresholds.

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
