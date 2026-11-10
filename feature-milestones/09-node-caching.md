# 9. Node caching

Reuse complete values at intermediate and final nodes while preserving each
handler's declared computation scope. At this milestone's start, the SQLite value
store delivered earlier supplies immutable results addressed directly by identity
keys, atomic completion, schemas,
and indexed grouped reads. This milestone adds the lookup, reduced execution, and
assembly behavior that consumes those guarantees.

## Accepted scope

### Identity and input contracts

A provider record is one immutable location identified by
`(dataset_id, revision, location_id)`, including a fixed unversioned identity when
revision is `None`. Provider node ID, code revision, resources, cache setting,
acquisition batch, and selection path do not change that identity. Changed record
contents require a different tuple; provider code revisions are reporting metadata.

Metrics and operations have author-managed implementation identity
`(node_id, code_revision)`, with an optional opaque revision and stable `None`
default. Authors change it for output-affecting implementation or declaration
changes. The same computation key must identify the same values and membership,
including emptiness, even with caching off. Do not prove determinism, hash outputs,
inspect code, or infer versions to establish that guarantee.

The shared key contract, already supplied by the value store, is:

| Descriptor | Contents |
| --- | --- |
| Location record | Identity-kind discriminator and dataset/revision/location tuple only |
| Metric computation | Discriminator, implementation identity, output scope, full-selection or single-partition computation scope, all effective parameters, every named input identity, direct-origin IDs/revisions, and primary-input name when location scoped |
| Operation computation | Metric components with no direct origins, plus declared target dataset ID/revision; implementation identity covers selection and mapping; resulting target IDs are outputs, not prerequisites for its own key |

Bind and check the declared input requirements against concrete dataset
registrations before the applicable cache lookup. Required schemas alone never
identify a cached input or result. Retain actual dataset/revision bindings even
for empty selections. Persistent lookup and execution-local memoization both use
bound computation identities, so two uses of the same metric with different
datasets cannot share results merely because their metric name or bare IDs match.
Binding does not acquire inputs or execute handlers solely to check a valid hit.

The operation's implementation revision covers its optional target selector and
mapping handler together. Source/dependency identities and effective parameters
cover both callbacks; the target dataset/revision fixes searchable membership and
record contents. Construct operation keys without running selectors or querying
targets. Candidate IDs, expression hashes, backend connection settings, and
independent selector revisions add no key inputs or separate query-cache entries.

Metric direct origins combine node declarations and resource-binding declarations,
deduplicated by `(id, revision)`. They are collected before cache lookup or resource
initialization. Their descriptions, declaration placement, and object identity do
not affect keys. Operations have no direct origins or resource bindings. Dataset
identity supplies the origin for provider resolution and managed target selection.
Upstream metric/operation revisions and origins propagate through their computation
keys; deployment build revisions, execution/job IDs, storage references, credentials,
and location/output content hashes are not additional key inputs.

Use complete Pydantic-normalized parameter values, including defaults and nulls;
equal omitted and explicit defaults have equal identity. Unsupported values and
non-finite numbers fail, with no string/repr fallback. Canonicalize unordered
identity collections, preserve validated scalar distinctions and ordered parameter
lists, then encode deterministic compact JSON with sorted object keys and hash its
UTF-8 bytes with SHA-256. The discriminator separates record and computation keys.
This normalization makes A/B and B/A selections equivalent without promising any
input/output order.

Named provider inputs contribute dataset/revision and selected IDs. Named metric
outputs and operation correspondences contribute compact upstream computation
keys, output names, and selected groups where applicable. Per-location producers
contribute each partition's key independently of batching; `off` and `full`
producers contribute the entire initially bound computation key even when a
consumer selects fewer groups. Unpartitioned outputs contribute a whole table
under its complete key. Correspondences used to select target records remain
dependencies after provider resolution. Never recursively embed ancestor
descriptors or erase a correspondence's computation identity.

Partitions are the distinct represented `location_id` values in the selected
primary input, qualified by dataset/revision. Incoming operation pairs may repeat
a target under different sources without changing its downstream partition.
Location-scoped output rows have non-null string `location_id` and `row_key`,
unique as a pair within each named output; owners must have been supplied to the
handler. Operations return string source-to-target-tuple mappings; their own
partitions belong to source keys. Unpartitioned outputs have no mandatory owners
or author row keys and remain complete shared tables.

### Strategies and eligibility

| Node or scope | Cache setting and behavior |
| --- | --- |
| Location-scoped metric or operation | `off` by default; `full` for complete bound selections; `per_location` for independently computable primary partitions |
| Unpartitioned metric | `off` by default or `full`; `per_location` is invalid |
| Provider | Boolean `False` by default; `True` reuses individual immutable location records |

When invoked, `off` executes its complete bound batch. `full` looks up that complete
computation and executes the batch on a miss. `per_location` looks up requested
partitions and executes only missing ones, which may share a handler call. Reuse
is available at both intermediate and final nodes; physical SQL partitioning alone
does not grant subset reuse to a full computation.

Reject invalid settings during declaration or plan validation before resource
acquisition. Partition ownership alone is not a promise of independent computation.
Authors choose and validate the independence required by `per_location`, including
dependencies and shared context. For selector-backed operations this promise
covers selection and mapping together: a source partition's result cannot change
when another source enters or leaves its physical batch, even if the shared
candidate pool changes. Lyra does not infer it, prove it, or silently choose a
different strategy.

One metric keeps the same declared cache policy across all accepted datasets.
Authors guarantee its independence and determinism for every supported binding;
Lyra trusts that promise without behavioral proof runs. For polygon temperature,
independent means each polygon's result depends on its own qualified record,
declared raster origin, parameters, and applicable dependencies. Normalizing
against all selected polygons requires that complete context in the computation's
inputs. Reusable schemas do not weaken the shared-input or upstream-scope rules.

With a `per_location` metric and all other effective inputs unchanged:

| Previous computation | Later request | Reuse |
| --- | --- | --- |
| 1990 tracts A/B | 1990 tracts B/C | Reuse B; compute C |
| 1990 tract A | 2000 tract A | Separate computation |
| Census tract A | City block A | Separate computation |
| Polygon A with raster revision r1 | Polygon A with raster revision r2 | Separate computation |

Equal geometries, schemas, or IDs across datasets do not authorize reuse. Full
computations still identify complete selections. A per-location consumer of a
full or unpartitioned upstream output still references that complete upstream
identity, so changing its selection can change the consumer's key even for an
unchanged primary location.

Disabled caching is node-local. An `off` metric/operation or uncached provider
executes whenever invoked, even if its values are retained. A valid downstream
hit may bypass that invocation entirely. Disabling caching does not delete retained
values, disable downstream caching, or permit different logical contents under
the same key.

For metrics and operations, `off` and `full` use the same complete-selection
computation identity. Switching between either of these strategies and
`per_location` changes computation scope and therefore computation keys. Dependent
keys also change where they reference those upstream identities. Previously stored
entries remain retained and reusable whenever the active strategy and computation
identity match.

### Consumer selection and upstream execution

Preserve each metric or operation producer's initial input binding for the request
before downstream cache hits reduce needed work. Initially bound inputs describe
that request's selection, not an entire dataset. Selection bindings determine
what the consumer receives; the producer's strategy determines how much it may
compute.

A partition-related input supplies rows associated with requested primary keys
through known correspondences; a shared input supplies its complete bound selection.
The primary input follows its partition declaration. Dependencies explicitly
forwarding it into their own partitioning input, and operations deriving targets
through mappings or declared chains, follow it automatically. Other unclear
relationships require an explicit shared binding or correspondence. Matching names,
datasets, types, or IDs alone is insufficient, and explicit shared selection may
override partition following. Reject partition-selection bindings on unpartitioned
outputs. These consumer rules apply under every node cache setting.

For a producer initially bound to A/B/C whose consumer now needs only B/C:

| Producer strategy | Required producer behavior |
| --- | --- |
| `off` | Execute the complete A/B/C binding when its output is needed |
| `full` | Look up the complete A/B/C computation; execute that selection on a miss |
| `per_location` | Request B/C, check their own keys, and compute only remaining misses |

The partition-related consumer receives B/C groups in every case. Apply this rule
at every upstream dependency. Shared inputs retain their complete bound selection,
including during reduced execution. Unpartitioned metrics likewise retain all
initially bound selections, and their outputs remain whole shared tables that
downstream misses cannot narrow.

Provider exact-ID acquisitions may narrow with either boolean setting because
immutable records are independently identified. With caching off, acquire the
records still needed; with it on, acquire only missing needed records and combine
them with hits.
Other upstream computations or shared inputs may still require A/B/C even if one
consumer needs only B/C. Empty needed ID selections invoke no resolver and
create no record keys. Batch records as appropriate without batch identity.

Run an optional target selector only when its operation requires computation.
For `off` and a `full` miss, select candidates for the complete initially bound
operation inputs. For `per_location` misses, select for the needed source
partitions while preserving complete shared inputs. A valid operation or downstream
hit bypasses selector construction, target-query execution, and mapping work that
is no longer needed, even if the target provider's own caching is disabled.

Individual cached records cannot answer a predicate query or prove a zero-match
selection. When selection is needed, execute its managed query to acquire the
complete matching set; provider record caching does not turn that query into
exact-ID misses. Reuse its validated records within the execution when forwarding
targets, and persist them under ordinary record keys when caching or retrieval
requires it. Later exact-ID acquisitions may reuse those entries. Do not add a
separate query-membership cache.

### Complete values and assembly

Metrics and operations share lookup, publication, retention, and reuse machinery.
Each per-location entry contains the complete partition across every named output,
including completed empty outputs. Full entries cover the complete bound selection
or all whole unpartitioned outputs. Header presence represents completion; missing
headers are misses. No partially completed output set is reusable.

The store publishes the value header, applicable partition records, and all named
output rows in one transaction after validation. The value key is the computation
or record key itself, without another storage ID, mapping table, or availability
flag. A header plus partition scope can therefore represent zero rows without a
separate completion object. Named metric outputs must all be present, including
schema-valid empty tables; missing output names do not become empty cache entries.

Omitted supplied operation keys normalize to completed empty groups after a
successful return. Operations cache source/target pairs under source partitions;
shared targets do not merge those groups. Providers require one resolved record
per key and have no empty-record cache values. Failed or unresolved work is not
published as empty success.

For a per-location request A/B/D with A/B cached, the handler receives only D's
primary rows, preserving distinct incoming correspondences. Partition-related
dependencies supply D's associated groups, and shared inputs remain complete.
Assembly returns all requested A/B/D groups using explicit keys. A full A/B/C entry
does not satisfy A/B/D; the latter is a distinct full computation.

Preserve metric `location_id`/`row_key` identities and qualified operation pairs
across fresh and reused fragments, without renumbering or row-position matching.
Forwarded targets remain repeated once per correspondence, while provider records
remain unique. Retrieve groups through indexed queries and reference retained
values without creating another assembled copy. Physical order has no semantic
meaning or public guarantee.

For identity, a per-location A result references `K_A` whether computed alongside
B or C when its inputs and shared context match. A selected A group from a full
or uncached A/B/C result still references `K_ABC`, and differs from A within
`K_ABD`. Changing an unpartitioned summary's input selection likewise changes
dependent keys even for per-location consumers.

Zero-location per-location requests have no partition computations. Persisted
full/uncached location-scoped computations may represent zero locations with a
header; unpartitioned empty-input computations still run when needed unless full
reuse supplies them, and may produce nonempty tables.

### Values independent of execution history

Matching requires only the permitted strategy, declared identity, complete values,
and schema/scope information needed for retrieval. Cache entries carry no required
historical invocation, calculation time, execution report, or reporting flag.
Retained values remain valid independently of the execution that first stored
them, including when a later phase of that execution fails.
Repeated persistence under one key retains the existing immutable value. Retain
stored data indefinitely; cache policy changes and expired jobs do not delete it,
and later enabled caching may reuse values retained while its own reuse was off.
Only required final results and intermediates needed for caching or ordinary
retrieval are persisted. Uncached intermediates can remain in memory without
snapshots or identity-only membership records for reporting.

Retain native table values privately and give handlers and callers independent
working frames under milestone 6's ownership contract. Changes to a working frame's
attributes, IDs, geometry, CRS, or metadata cannot change a retained value, a later
cache result, or the original input selection used for validation. Dataset scope
and computation keys come from runtime bindings and stored descriptions, not frame
attributes. Cached immutability is a logical value guarantee, not a requirement
for custom frozen Python containers or serialized buffers in memory.

Preserve actual runtime outcomes for subsequent reporting: hits do not imply a
handler ran, partial hits can coexist with execution, and bypassed work is skipped.
A shared dependency executing for another consumer is not globally skipped.
Do not execute extra work to recover historical producers or fill report details.

## Completion boundary

Intermediate and final metrics and operations support their declared strategies,
providers support boolean caching, and hybrid graphs preserve required inputs
while reusing values. Reports are not prerequisites for valid hits.

Isolated behavioral checks cover complete hits, partial hits, misses, empty results,
zero-location requests, omitted operation keys, shared targets, multiple named
outputs, and unpartitioned shared context. They also verify the A/B/C versus B/C
upstream-scope distinction, provider narrowing with both settings, downstream
bypass of uncached nodes, revision and origin changes, order-independent identity,
and retained values across cache-setting changes. Selector-backed cases cover
operation/target revision invalidation, complete and partial acquisition bypass,
source-batch independence, and queries still executing when only individual target
records are cached. Measure representative execution and memory behavior through
existing benchmarks without pinning incidental helper call counts. Include
mutation isolation across consumers and repeated reads, independently of whether
values were cached or freshly computed; preserve native dtypes, empty schemas,
geometry, and CRS without custom snapshot or fingerprint assertions.

Exercise one reusable metric across different providers and dataset bindings:
overlapping selections within one dataset reuse valid partitions; equal bare IDs
across datasets or revisions do not. Cover changed raster origins and complete
shared dependencies, full-upstream selection changes, two differently bound uses
of one metric in the same graph, and empty selections retaining scope. Verify
cached and freshly computed outputs retain their actual owning dataset without
introducing another strategy or proving author independence.

## Deferred or excluded work

[Milestone 10](10-execution-reports-and-durable-success.md) exposes current execution
and mixed reuse outcomes in durable reports. Caching does not require historical
producer links, per-row invocation records, or a durable event for every partition.

Automatic determinism or independence proofs, output-content hashing, automatic
code-change detection, automatic cache clearing, per-entry invalidation controls,
and cleanup policies are outside this milestone. Stored intermediate values follow
ordinary caching/retrieval needs, without trace-only snapshots or membership tables.

## Reference points

The current [cache implementation](../src/lyra/runtime/cache.py) and
[executor](../src/lyra/runtime/execution.py) are the main runtime boundaries for
lookup, scheduling, and assembly.
