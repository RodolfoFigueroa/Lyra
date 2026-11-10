# 8. Node caching

Reuse complete values at intermediate and final nodes while preserving each
handler's declared computation scope. This milestone completes the caching and
assembly behavior in decisions 3, 4, 9, 11, and 12 and R1–R5 of
[the ledger](../DECISIONS.md), using the identities and SQLite store established
in [milestone 7](07-sqlite-result-storage.md).

## Accepted scope

### Strategies and eligibility

| Node or scope | Cache setting and behavior |
| --- | --- |
| Location-scoped metric or operation | `off` by default; `full` for complete bound selections; `per_location` for independently computable primary partitions |
| Unpartitioned metric | `off` by default or `full`; `per_location` is invalid |
| Provider | Boolean `False` by default; `True` reuses individual immutable location records |

Reject invalid settings during declaration or plan validation before resource
acquisition. Partition ownership alone is not a promise of independent computation.
Authors choose and validate the independence required by `per_location`, including
dependencies and shared context. Lyra does not infer it, prove it, or silently
choose a different strategy.

Disabled caching is node-local. An `off` metric/operation or uncached provider
executes whenever invoked, even if its values happen to be retained. A valid
downstream hit may bypass the invocation entirely. Disabling a node's caching
does not disable dependent caching, invalidate results, change identity, or permit
different logical contents under the same key.

### Consumer selection and upstream execution

Preserve each metric or operation producer's initial input binding for the request
before downstream cache hits reduce needed work. Initially bound inputs describe
that request's selection, not an entire dataset. Selection bindings determine
what the consumer receives; the producer's strategy determines how much it may
compute.

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

Provider acquisitions may narrow with either boolean setting because immutable
records are independently identified. With caching off, acquire the records still
needed; with it on, acquire only missing needed records and combine them with hits.
Other upstream computations or shared inputs may still require A/B/C even if one
consumer needs only B/C. Empty needed selections invoke no provider handler and
create no record keys. Batch records as appropriate without batch identity.

### Complete values and assembly

Metrics and operations share lookup, publication, retention, and reuse machinery.
Each per-location entry contains the complete partition across every named output,
including completed empty outputs. Full entries cover the complete bound selection
or all whole unpartitioned outputs. Header presence represents completion; missing
headers are misses. No partially completed output set is reusable.

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

Use the descriptor rules from milestone 7 for every named dependency, including
operation correspondences used in selections and complete shared context.
Per-location identities do not depend on batching; full and uncached producer
identities retain the complete initially bound scope. Changing an unpartitioned
summary's input selection changes dependent keys even for per-location consumers.

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
and retained values across cache-setting changes. Measure representative execution
and memory behavior through existing benchmarks without pinning incidental helper
call counts.

## Deferred or excluded work

[Milestone 9](09-execution-reports-and-durable-success.md) exposes current execution
and mixed reuse outcomes in durable reports. Caching does not require historical
producer links, per-row invocation records, or a durable event for every partition.

Automatic determinism or independence proofs, output-content hashing, automatic
code-change detection, automatic cache clearing, per-entry invalidation controls,
and cleanup policies are outside this milestone. Stored intermediate values follow
ordinary caching/retrieval needs, without trace-only snapshots or membership tables.
