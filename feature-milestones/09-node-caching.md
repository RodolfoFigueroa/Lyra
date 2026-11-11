# 9. Node caching

Reuse complete values at intermediate and final nodes while preserving each
handler's declared computation scope. At this milestone's start, the SQLite value
store delivered earlier supplies immutable results addressed directly by identity
keys, atomic completion, schemas,
and indexed grouped reads. This milestone adds the lookup, reduced execution, and
assembly behavior that consumes those guarantees.

## Accepted scope

### Identity and input contracts

A base provider record is one immutable location identified by
`(provider_id, code_revision, origin identities, location_id)`. The first three
components form its provider scope. Origin identities are deduplicated and unordered;
an omitted origin revision denotes fixed unversioned data. Provider code and origin
revisions participate in record and downstream computation keys. Resource aliases,
credentials, cache settings, acquisition batches, and selection paths add no identity
inputs. Changed base record contents or collection membership require a changed
provider scope under milestone 5's contract.

Metrics and operations have author-managed implementation identity
`(node_id, code_revision)`, with an optional opaque revision and stable `None`
default. Authors change it for output-affecting implementation or declaration
changes. The same computation key must identify the same values and membership,
including emptiness, even with caching off. Do not prove determinism, hash outputs,
inspect code, or infer versions to establish that guarantee.

The shared key contract, already supplied by the value store, is:

| Descriptor | Contents |
| --- | --- |
| Base location record | Identity-kind discriminator, provider scope, and location ID |
| Metric computation | Discriminator, implementation identity, output scope, full-selection or single-partition computation scope, all effective parameters, every named input identity, direct-origin IDs/revisions, and primary-input name when location scoped |
| Operation computation | Discriminator, implementation identity, location scope, full-selection or single-partition computation scope, all effective parameters, sole named source binding identity, and declared target provider scope; implementation identity covers selection and mapping; resulting target IDs are outputs, not prerequisites for its own key |

Resolve declared provider requirements and scopes before the applicable cache
lookup, retaining scope even for empty selections. Persistent lookup and
execution-local memoization both use bound computation identities, including
effective parameters and named input identities under the computation-scope rules
below. Changed provider code or origin revisions remain distinct even when bare
IDs match.
Binding does not acquire inputs or execute handlers solely to check a valid hit.

[Milestone 14](14-interventions.md) extends location-input and operation-target
identities with the applicable intervention-set identity. Added records also
include that identity in their keys; unchanged base records remain reusable under
their ordinary keys. A changed effective target catalog changes an operation key
before candidate IDs are known, with changes propagated through existing
dependencies. Interventions do not become a global invalidation token for
unrelated computations or introduce another cache policy.

Successful cache reuse requires complete intervention validation under milestone
14, including interventions outside the selected data. That validation may acquire
base data before accepting a result even when computation is bypassed. Established
complete validation is reusable only with the same base and set identities; a
computation hit or unrelated retained records alone cannot replace it. Key
construction itself remains independent of selector execution and acquisition.

The operation's implementation revision covers its optional target selector and
mapping handler together. The sole source binding's identity, including upstream
operation provenance, and all effective parameters cover both callbacks; the
target provider scope fixes base searchable membership and record contents, with
effective membership additionally qualified by interventions when present.
Construct operation keys without running selectors or querying targets. Candidate IDs,
expression hashes, backend connection settings, and
independent selector revisions add no key inputs or separate query-cache entries.
Callback table argument names do not replace the declared source name in identity.

Collect direct origins from metric and provider declarations before cache lookup
or resource initialization, deduplicating by `(id, revision)`. Their descriptions,
order, and object identity do not affect keys. Operations have no direct origins
or resource bindings. Provider scopes include the origins used by both exact-ID
resolution and managed target selection.
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

Named provider inputs contribute provider scope and selected IDs. Named metric
outputs and operation correspondences contribute compact upstream computation
keys, output names, and selected groups where applicable. Per-location producers
contribute each partition's key independently of batching; `off` and `full`
producers contribute the entire initially bound computation key even when a
consumer selects fewer groups. Unpartitioned outputs contribute a whole table
under its complete key. Correspondences used to select target records remain
dependencies after provider resolution. Never recursively embed ancestor
descriptors or erase a correspondence's computation identity.

Partitions are the distinct represented `location_id` values in the selected
primary input, qualified by provider scope. Incoming operation pairs may repeat
a target under different sources without changing its downstream partition.
Location-scoped output rows have non-null string `location_id` and `row_key`,
unique as a pair within each named output; owners must have been supplied to the
handler. Operations return string source-to-target-tuple mappings; their own
partitions belong to source keys. Unpartitioned outputs have no mandatory owners
or author row keys and remain complete shared tables.

### Strategies and eligibility

Metrics without a location input marked `primary=True` are unpartitioned under
milestone 6. Exactly one marked location input establishes location ownership
while leaving caching at its `off` default. Choosing `per_location` is a separate
opt-in with the independence contract below; it does not designate a primary
input or change the derived scope.

| Node or scope | Cache setting and behavior |
| --- | --- |
| Location-scoped metric or operation | `off` by default; `full` for complete bound selections; `per_location` for independently computable primary partitions |
| Unpartitioned metric | `off` by default or `full`; `per_location` is invalid |
| Provider | Boolean `False` by default; `True` reuses individual immutable location records |

Metric and operation decorators use the `cache` keyword, accepting exactly
`"off"`, `"full"`, or `"per_location"` subject to output-scope eligibility, with
`"off"` as the default. Location providers use a `cache` class attribute accepting
only `False` or `True`, defaulting to `False`. Reject other values without coercion
or aliases, including booleans on metrics/operations and strings on providers.

Cache policy belongs to node declarations. Local `run()`, `run_development()`,
and remote execution all honor those policies. Development execution can reuse
cached values and does not automatically force recomputation. Requests supply
input selections and calculation parameters, with intervention sets added by
milestone 14; per-run cache controls are deferred.
Remove the request-level `cache_revision` argument and field from local and remote
submission contracts and the normalized worker handoff. Implementation changes
use declared `code_revision` tokens, external data changes use origin/provider
identities, and calculation differences use effective parameters.

When invoked, `off` executes its complete bound batch. `full` looks up that complete
computation and executes the batch on a miss. `per_location` looks up requested
partitions and executes only missing ones, which may share a handler call. Reuse
is available at both intermediate and final nodes; physical SQL partitioning alone
does not grant subset reuse to a full computation.

Reject invalid settings during declaration or plan validation before resource
acquisition. Partition ownership alone is not a promise of independent computation.
Authors choose and validate the independence required by `per_location`; for
metrics this includes dependencies and shared context. Operations use their sole
source, optional candidates, and optional typed parameters. For selector-backed
operations the promise covers selection and mapping together: a source partition's
result cannot change when another source enters or leaves its physical batch,
even if the shared candidate pool changes. Lyra does not infer it, prove it, or
silently choose a different strategy.

Each metric declares its cache policy. Authors guarantee the corresponding
independence and determinism; Lyra trusts that promise without behavioral proof
runs. For a block-temperature metric, independent means each block's result
depends on its own qualified record, declared raster origin, parameters, and
applicable dependencies. Normalizing against all selected blocks requires that
complete context in the computation's inputs.

For a `per_location` metric declared for `city_blocks`, with only the listed
inputs changing:

| Previous computation | Later request | Reuse |
| --- | --- | --- |
| Blocks A/B under one provider scope | Blocks B/C under the same scope | Reuse B; compute C |
| Block A with source origin revision r1 | Block A after that origin changes to r2 | Separate computation |
| Block A with provider code revision c1 | Block A after output-affecting provider changes to c2 | Separate computation |
| Block A with raster revision r1 | Block A with raster revision r2 | Separate computation |

Equal geometries, schemas, or IDs across provider scopes do not authorize reuse. Full
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
that request's selection, not a provider's entire collection. The automatic input
rules in [milestone 6](06-unified-graph-execution.md) determine what the consumer
receives; the producer's strategy determines how much it may compute.

The primary input follows the locations requiring computation. A dependency
follows those locations when its resolved binding connects the consumer's primary
input to the producer's primary input, directly or through declared operation
correspondences and chains. Resolve explicit bindings and matching-name forwarding
before determining that relationship. An operation's sole source is automatically
primary. A connection only to a metric producer's non-primary input does not
establish partition following. Coincidental names, providers, schemas, or IDs do
not establish an association.

Other metric inputs retain their complete originally bound data as shared context.
Unpartitioned outputs always remain complete, including when bound through primary
inputs. All retained shared context contributes to dependent computation identities;
changing that context can invalidate a location's cached result even when its own
record is unchanged. These consumer rules apply under every node cache setting.

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
source selection. For `per_location` misses, select for the needed source
partitions with the same effective operation parameters and retained upstream
source identity. A valid operation or downstream hit bypasses selector
construction, target-query execution, and mapping work that is no longer needed,
even if the target provider's own caching is disabled.

When interventions apply, effective selection excludes removed records and includes
only additions selected by the ordinary lookup or predicate. Reduced execution
does not narrow validation of the supplied sets, and provider cache hits cannot
restore a record removed from the effective view. Intervention validation is
separate from the selector, candidate-query, and mapping work bypassed above.

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
primary rows, preserving distinct incoming correspondences. For metrics,
partition-related dependencies supply D's associated groups, and shared inputs
remain complete.
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

[Milestone 12](12-retained-location-inputs.md) adds ordinary retrieval of root
location inputs, retaining their reconstruction references with metric values so
a cached dependency can later serve as a root. This later retention requirement
preserves computation identities and scheduling: hits need no historical report,
provider reacquisition, or handler execution to populate input inspection.
Persisting provider records for retrieval does not authorize execution reuse when
provider caching is disabled. Known empty inputs and inputs never materialized
because work was skipped follow that milestone's explicit availability contract.

Retain native table values privately and give handlers and callers independent
working frames under milestone 6's ownership contract. Changes to a working frame's
attributes, IDs, geometry, CRS, or metadata cannot change a retained value, a later
cache result, or the original input selection used for validation. Provider scope
and computation keys come from runtime bindings and stored descriptions, not frame
attributes. Cached immutability is a logical value guarantee, not a requirement
for custom frozen Python containers or serialized buffers in memory.

Preserve actual runtime outcomes for subsequent reporting: hits do not imply a
handler ran, partial hits can coexist with execution, and bypassed work is skipped.
A shared dependency executing for another consumer is not globally skipped.
Do not execute extra work to recover historical producers or fill report details.

## API sketches

### Metric caching

This declaration fragment reuses `AreaOutputSchema` and the independent block-area
calculation from [milestone 6](06-unified-graph-execution.md). Handler bodies are
omitted to focus on cache declarations.

```python
import geopandas as gpd
import pandas as pd

from lyra.sdk import LocationDependency, LocationProvider, location_operation, metric


@metric(
    name="block_area",
    inputs={
        "blocks": LocationDependency(provider="city_blocks", primary=True),
    },
    outputs={"area": AreaOutputSchema},
    cache="per_location",
)
def block_area(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    ...
```

`primary=True` establishes output ownership and leaves caching at `"off"` when
`cache` is omitted. Selecting `cache="per_location"` is the author's explicit
promise that each partition can be computed independently of other primary
partitions sharing the handler call, under the declared inputs and shared context.
Lyra trusts that promise without behavioral proof runs.

Use `cache="full"` to reuse the complete bound computation or `cache="off"` to
state the default explicitly. A metric without a primary location input accepts
only `"off"` or `"full"`, even when it has one location input.

### Operation caching

Operations use the same keyword and strategy strings. Their sole source is
automatically primary. This fragment uses milestone 6's independent county mapping:

```python
@location_operation(
    name="county_for_blocks",
    inputs={"blocks": LocationDependency(provider="city_blocks")},
    target_provider="census_counties",
    cache="per_location",
)
def county_for_blocks(
    blocks: gpd.GeoDataFrame,
) -> dict[str, tuple[str, ...]]:
    ...
```

For an operation with a `target_selector`, the `"per_location"` independence
promise covers selection and mapping together. A source partition's result cannot
change when other sources enter or leave the physical batch, even if the candidate
pool changes. Omitting `cache` leaves operation caching at `"off"`.

### Provider caching

Providers declare boolean caching alongside their existing class attributes.
This fragment reuses `CENSUS` and `TRACT_SCHEMA` from
[milestone 5](05-location-providers-and-origins.md):

```python
class CensusTracts(LocationProvider):
    id = "census_tracts"
    origins = (CENSUS,)
    schema = TRACT_SCHEMA
    cache = True

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        ...
```

`cache=True` enables reuse of individual immutable records. `cache=False` states
the default explicitly. Values such as `cache=True` on a metric or operation and
`cache="full"` on a provider are invalid. Invalid values and scope combinations
fail during declaration or plan validation before resource acquisition.

### Operation hits and target records

Use [milestone 7](07-operation-target-selection-with-ibis.md)'s
`mature_trees_near_blocks` operation with `cache="per_location"` and its `Trees`
provider with `cache=True`. For a source partition with unchanged identities and
effective parameters:

| Operation cache lookup | Required work |
| --- | --- |
| Hit, including a completed empty correspondence | Reuse the correspondence; skip selector construction, the target query, and the mapping handler. |
| Miss, even when individual target records are cached | Run the selector, query the complete matching candidate set, and run the mapping handler. |

The operation cache retains correspondences; the provider cache retains records
by ID. Cached records alone do not establish complete predicate membership.
Target records needed for forwarding follow the provider's ordinary acquisition
and reuse rules, so skipping selection does not itself guarantee that all
acquisition is skipped. Candidate records returned by a query are reused within
that execution. These cases use the existing record and computation keys without
additional cache settings or a query-membership cache.

These examples hold effective dataset identities fixed. When milestone 14's
interventions are present, their validation remains required independently of
the computation work listed in the table.

### Execution with declared policies

With the metric and providers registered on the runner and remote deployment,
ordinary execution calls use their declared cache policies:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]},
}

result = runner.run("block_area", arguments)

development_result = runner.run_development("block_area", arguments)

handle = client.jobs.submit("block_area", arguments)
```

Each call starts a separate execution. `run_development()` uses the same cache
policy as `run()` and may reuse the first call's retained values. Remote execution
applies the registered nodes' policies using its associated store. Callers select
inputs and calculation parameters; authors declare cache policies and the code
and data identities that determine whether retained values match.

### Partial hits and assembled results

Use the independent `block_area` metric above with `cache="per_location"` and
an initially empty store. Keep implementation revisions, provider scope, and
effective parameters unchanged across these requests:

```python
first = runner.run(
    "block_area",
    {"blocks": {"provider": "city_blocks", "ids": ["A", "B"]}},
)

second_arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["B", "C"]},
}
second = runner.run("block_area", second_arguments)
third = runner.run("block_area", second_arguments)

area = second.tables["area"]
```

| Execution | Primary locations supplied to the handler | Returned area table |
| --- | --- | --- |
| `first` | A and B | Areas for A and B |
| `second` | C | Areas for B and C |
| `third` | Handler is skipped | Areas for B and C |

Missing partitions may be batched; these sets imply no invocation count or row
order. The handler receives native tables for the work requiring computation.
The runtime combines B's retained area with C's newly computed area into the
ordinary `RunResult`. Each execution receives its own distinct result reference,
including `third`, under milestone 8's contract.

For the same request sequence with `block_area` declared `cache="full"` and an
initially empty store, the first call computes A/B and the second computes the
complete B/C selection. The retained A/B computation cannot satisfy B/C. The
third call reuses the complete B/C computation and skips the handler.

### Complete reference context during partial hits

Use milestone 6's `compare_block_areas` declaration with both that metric and its
`block_area` producer declaring `cache="per_location"`. Suppose B's comparison
with reference blocks X/Y is already cached, while C's comparison is missing:

```python
comparison_result = runner.run(
    "compare_block_areas",
    {
        "blocks": {"provider": "city_blocks", "ids": ["B", "C"]},
        "reference_blocks": {"provider": "city_blocks", "ids": ["X", "Y"]},
    },
)
comparison = comparison_result.tables["comparison"]
```

The comparison handler receives:

| Input | Supplied records |
| --- | --- |
| `blocks` | C |
| `areas` | C's area |
| `reference_blocks` | X and Y |
| `reference_areas` | X's and Y's areas |

The returned comparison combines B's retained rows with C's fresh rows. The
primary input and its related dependency rows follow the missing partition;
independent reference inputs retain their complete bound selections. Changing
the reference selection from X/Y to X/Z changes the comparison identities, so
B's comparison against X/Y cannot satisfy the new request. Unchanged upstream
area computations can still be reused under their own keys.

## Completion boundary

Intermediate and final metrics and operations support their declared strategies,
providers support boolean caching, and hybrid graphs preserve required inputs
while reusing values. Reports are not prerequisites for valid hits.

Verify that metrics without a primary marker reject `per_location` before
acquisition, including metrics with only one location input. Marking a location
input primary establishes location scope while retaining `off` caching unless
the author selects another strategy. Verify omitted and explicit defaults,
decorator and provider-class declarations, and rejection of unsupported values
without coercion or aliases.
Verify that local, development, and remote execution honor the declared policies,
including development cache hits, and that submission contracts reject the
request-level `cache_revision` control.

Isolated behavioral checks cover complete hits, partial hits, misses, empty results,
zero-location requests, omitted operation keys, shared targets, multiple named
outputs, and unpartitioned shared context. They also verify the A/B/C versus B/C
upstream-scope distinction, provider narrowing with both settings, downstream
bypass of uncached nodes, revision and origin changes, order-independent identity,
and retained values across cache-setting changes. Selector-backed cases cover
invalidation from operation code, target-provider code, or origin revision changes.
Verify invalidation from changed operation parameters and upstream operation
identities carried by the sole source binding, including across chained operations.
Equal omitted and explicit defaults reuse the same computation; metric-only
parameters absent from the operation's model do not change its key when the source
binding is unchanged.
They also cover complete and partial acquisition bypass, source-batch independence,
and queries still executing when only individual target records are cached.
Measure representative execution and memory behavior through
existing benchmarks without pinning incidental helper call counts. Include
mutation isolation across consumers and repeated reads, independently of whether
values were cached or freshly computed; preserve native dtypes, empty schemas,
geometry, and CRS without custom snapshot or fingerprint assertions.

Exercise separate concrete block and tract metrics with different providers;
equal bare IDs across their provider scopes do not authorize reuse. For each metric,
overlapping selections within one scope reuse valid partitions, while changed
provider code or origin revisions do not. A shared origin revision change invalidates
both providers' records and their dependents; changing one provider's code revision
affects only computations depending on that provider. Cover equivalent origin sets,
resource alias changes, changed raster origins, and complete shared
dependencies, full-upstream selection changes, two uses of one metric with
different ID selections or parameters in the same graph, and empty selections
retaining scope. Verify cached and freshly computed outputs retain their owning
provider scope without proving author independence.

Verify partial hits with automatically related area inputs and independent
reference inputs: for main blocks A/B with A cached, the consumer receives B's
records and areas plus the complete reference context. Explicit and matching-name
bindings yield equivalent behavior; coincidental providers or IDs and bindings
only through a producer's non-primary input do not narrow reference context.
Changing that context changes dependent keys. Exercise operation-derived reference
groups with overlapping targets and complete unpartitioned dependencies under
partial reuse, preserving each upstream producer's `off`, `full`, or `per_location`
execution scope.

## Deferred or excluded work

[Milestone 10](10-execution-reports-and-durable-success.md) exposes current execution
and mixed reuse outcomes in durable reports. Caching does not require historical
producer links, per-row invocation records, or a durable event for every partition.

Per-run cache bypass, refresh, and force-recompute controls are deferred. Any future
read-bypass option must preserve each node's computation scope, immutable
identities, and persistence rules.

Automatic determinism or independence proofs, output-content hashing, automatic
code-change detection, automatic cache clearing, per-entry invalidation controls,
and cleanup policies are outside this milestone. Stored intermediate values follow
ordinary caching/retrieval needs, without trace-only snapshots or membership tables.

## Reference points

The current [cache implementation](../src/lyra/runtime/cache.py) and
[executor](../src/lyra/runtime/execution.py) are the main runtime boundaries for
lookup, scheduling, and assembly.
