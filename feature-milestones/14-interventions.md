# 14. Interventions

Let callers add and remove locations in a request-specific view of registered
datasets, then observe the resulting computations through ordinary results,
reports, retained inputs, and exports. Build on the existing provider, selection,
execution, identity, and retention contracts. Interventions leave the base
datasets unchanged and require no special handler returns or row-level tracing.

## Accepted scope

### Intervention sets and target catalogs

A managed request may supply multiple intervention sets, with at most one set
per catalog. Each set has a caller-supplied stable ID and revision, identifies
its target catalog, and contains additions and removals. Both the collection of
sets and the interventions within a set are unordered. Their order has no effect
on behavior or identity. Reject requests supplying multiple sets for one catalog;
callers combine their desired changes into one set before submission.

A target catalog identifies one registered location provider by its public
provider ID. It does not identify a schema, resource configuration key, or a new
registry. An unknown catalog fails validation. Resolve and retain the provider's
base scope, including its code revision and declared origin identities, through
the existing preparation and worker-handoff contracts.

Apply each set consistently wherever its catalog participates in managed location
access during the run, including direct inputs, operation sources, and operation
targets. Sets are request data, not metric parameters or dependency overrides.
Local, development, and remote execution share this contract, including both
Python client modes, HTTP, and MCP. A request without interventions preserves
ordinary execution behavior.

Capture accepted sets and their record values independently of mutable caller
objects. Worker transport, execution, identity construction, and reporting use the
same accepted contents. Caller mutations after acceptance cannot alter the run.
Handlers continue receiving independent native working tables under milestone 6's
ownership rules.

### Additions, removals, and author-prepared replacements

An addition supplies a complete record satisfying the target provider's registered
schema. Every declared column must be present, including nullable attributes;
nullable values remain subject to that schema. Apply the ordinary ID, type,
geometry, CRS, nullability, and column validation rules. Interventions introduce
no automatic reprojection, attribute inference, or relaxed record schema.

Every added ID must be absent from the entire base dataset and distinct among
additions in that set. Selection boundaries do not limit collision checks.
Different catalogs and independently identified intervention sets may reuse the
same bare ID. Their qualified identities distinguish the records, including when
separate revisions supply different attributes for that ID.

A removal supplies only the ID of an existing base record. A nonexistent ID
fails. Validate removals against the original base dataset; an addition cannot
make a removal valid. Removing an existing ID does not free it for an addition
in the same set. These rules establish an effective dataset containing the base
records not removed together with the added records, independently of application
order.

Authors may prepare a replacement externally, remove the original record, and
add the complete replacement under a new ID. Lyra does not fetch and modify the
original on their behalf, infer a replacement relationship, redirect references,
or provide a separate update operation or helper. Relationships remaining in
other records retain their ordinary values and resolution behavior.

### Selection and operation behavior

Normal location selection operates against the effective dataset. Exact-ID
requests may resolve added records. Predicate selection includes additions only
when they satisfy the same predicate as base records and excludes removals.
Returning the target relation without a filter selects the complete effective
dataset. Additions are not appended unconditionally to selected tables, and
selection does not create implicit requests for every added ID.

Explicit requests for removed IDs fail as unavailable locations. This also applies
when an operation derives a removed target ID from its source records. Predicate
selection simply excludes removed records. Preserve the existing distinction
between failed exact-ID resolution and a successfully completed empty selection.

Operations receive their effective source selections and select against their
effective target catalogs. Source and target catalogs may have different sets.
Candidate predicates retain the supported backend's attribute, spatial, and null
semantics for both base records and additions. Managed adapters own this behavior;
authors continue constructing the same restricted single-provider filters.
Applying the effective view internally does not authorize additional relations or
broader expressions in author-written selectors, or a Python filtering fallback.

Mapping handlers choose correspondences through the ordinary operation contract.
For selector-backed operations, returned targets must belong to the validated
effective candidate set. Operations without selectors use effective exact-ID
resolution. Preserve unique targets within each source group, repeated targets
under different sources, completed empty groups, and both immediate source and
target scopes. Forward authoritative effective records without reapplying a set
to each batch or correspondence. Operations do not create or register locations.

### Complete validation and failure behavior

Validate request structure, target registrations, and schema requirements at the
appropriate shared boundaries. Structural and plan validation require no external
acquisition. Checks of base-record existence and absence belong to managed runtime
acquisition and may initialize the ordinary provider resources after plan
validation. Support these checks independently of the selected subset; strict
exact-ID resolution still fails on missing requested records. Do not require a
full dataset copy for intervention validation. The public membership-check
interface is specified with the intervention APIs.

Establish validity for every supplied set before downstream computation receives
intervened inputs or the run accepts a cached result as successful. Validate even
interventions that the requested selections would exclude or that skipped work
would never use. Empty selections and cache hits do not waive these checks.
Any invalid intervention fails the request as a whole; consumers do not receive a
partially accepted collection of sets. Provider and adapter failures remain
failures rather than evidence that a record is absent.

Complete validation previously established for the same base dataset identity and
intervention-set identity may be reused. A changed base scope requires validation
against that scope, even when the supplied set has not changed. A computation
cache hit or a collection of retained location records alone does not establish
complete intervention validity. Validation failures follow ordinary execution
error paths and do not publish successful results or reports.

### Immutable identity, storage, and caching

The target catalog, set ID, and revision identify immutable intervention contents.
Changing additions, removals, or added attributes requires a changed set identity.
Reordering contents does not. This is the caller's identity promise, following
the existing author-managed immutability contract; it does not introduce content
hashes, automatic revision inference, or behavioral proof runs.

An effective dataset is identified by its base provider scope and the applicable
intervention-set identity. Keep the base provider's declared identity intact.
When no set applies to a catalog, retain its existing base identities.
Unchanged base records retain their ordinary record keys and remain reusable.
Added records are qualified by the base scope, intervention-set identity, and
location ID, so independently identified sets cannot overwrite each other's
records. A removed record is absent from this effective view; its base record
and any previously retained values remain unchanged.

Bound location inputs retain their effective dataset identities. Operation keys
also include the effective identity of the declared target catalog before the
selector executes or candidate IDs are known. Changes propagate through the
existing computation dependencies, including when selected bare IDs remain the
same. Interventions on a catalog are not an unrelated global invalidation token
for computations with no dependency on that catalog.

Use the existing record and computation key machinery, typed store, and cache
policies. Preserve complete shared inputs, full and per-location computation
scope, partial reuse, empty completion, and correspondence ownership. Cache
reuse must respect the effective input and target identities as well as required
intervention validation. Retaining or validating an added record does not imply
that an external provider acquired it. No new per-request cache controls or
independent query-membership cache are introduced.

### Reports, retained inputs, and exports

Retain the complete accepted intervention request with the normalized request in
the execution report, including records outside the selected computation.
Descriptions preserve base provider identities and applicable intervention-set
identities for effective inputs, operation targets, and output ownership. Keep
source and target meanings distinct, including empty selections and cached work.
No additional reporting columns are required in handler or output tables.

Report actual work at its runtime boundary. Base-membership validation can acquire
data even when the subsequent computation is reused. Distinguish that acquisition
from selector construction, candidate queries, and mapping or metric execution.
Caller-supplied additions do not count as external record acquisition. Reusing
established validation must not claim fresh acquisition. The existing successful
report and durable-publication contract remains authoritative.

Available retained inputs reconstruct the complete logical effective selections
associated with the result, including added records, correspondence pairs, and
cached and fresh portions. Persist the records and references required for that
ordinary retrieval without storing another copy of the entire effective catalog.
Membership checks during validation do not by themselves establish a selected
input binding or upgrade an input marked not materialized. Preserve milestone
12's availability and integrity rules.

Retrieval and exports use retained values and descriptions without consulting the
current base dataset, rerunning selections, or reapplying interventions. Existing
Python, HTTP, MCP, Arrow, and GeoParquet interfaces carry the effective identities
alongside their ordinary data. The normalized request retains what was submitted;
input retrieval exposes what was selected. Outputs retain their existing ownership
rules, so intervention IDs need not appear in every affected result. No comparison
or intervention-specific result interface is required.

## API sketch

Supply intervention sets through the managed Python call's `interventions`
keyword. Each `Intervention` represents the complete change set for one catalog.
Its `id`, `catalog`, and `revision` retain the immutable set identity described
above. Additions are grouped in one ordinary GeoDataFrame; removals are a list
of strict string location IDs.

Assume `metric_name` and `arguments` identify the ordinary managed request, and
`df_additions` is a GeoDataFrame prepared against the registered `trees` provider's
schema:

```python
run(
    metric_name,
    arguments,
    interventions=[
        Intervention(
            id="planting-proposal-a",
            catalog="trees",
            revision="v1",
            add=df_additions,
            remove=["tree_1", "tree_2"],
        )
    ],
)
```

Either action may be omitted. Omitting `add` means no additions; omitting `remove`
means no removals. A removal-only set requires no empty GeoDataFrame. An explicitly
supplied empty additions frame must still satisfy the provider's complete schema,
including its declared columns, active geometry, and CRS.

The additions frame uses the provider's `location_id`, `geometry`, and attribute
columns. Its index and row order have no semantic significance; IDs come from
`location_id`, and duplicate added IDs fail validation. Removal-list order is
likewise insignificant. Authors can construct and concatenate ordinary frames
without per-record intervention objects or a custom table wrapper.

Authors may validate the additions frame themselves using the provider's existing
Pandera schema before submission. Lyra still performs authoritative validation
against that schema, separately from the base-dataset checks establishing that
added IDs are absent and removed IDs exist. Submission captures independent frame
and removal-list values, so later caller mutations cannot change the accepted
request.

Remote Python clients accept the same GeoDataFrame input and serialize its values
for submission. The corresponding HTTP/MCP representation and frame serialization
remain to be discussed; they must preserve the declared values and CRS and support
the same authoritative validation.

## Completion boundary

Local, development, and remote execution support the same intervention semantics
through their shared runtime and request contracts. Verify consistent behavior
through synchronous and asynchronous Python clients, HTTP, and MCP, including
accepted-request ownership and worker transport. Requests without interventions
retain their existing behavior.

Cover omitted additions and removals, schema-valid and invalid empty additions
frames, duplicate added IDs, and index and row-order independence. Caller mutations
to submitted frames and removal lists must not change accepted requests or results.

Verify schema validation, complete base-dataset collision and existence checks,
unknown catalogs, conflicting catalog sets, and failures outside selected data.
Check that invalid interventions fail before downstream computation or successful
cache reuse, including empty and skipped execution paths. Verify reusable complete
validation only under unchanged base and set identities. Failures of acquisition
must not become successful absence checks or partially accepted requests.

Exercise order-independent behavior and identities, distinct scenarios and
revisions sharing bare added IDs, base revision changes, and unchanged base-record
reuse. Cover full and per-location caching, partial hits, complete shared inputs,
effective target identity before candidate discovery, and propagation through
operation chains. Changed interventions must not reuse incompatible computations.

Verify exact-ID and supported predicate selection of effective records, including
attribute, spatial, and null semantics on both managed adapters. Cover source and
target interventions, selector-backed and ID-only operations, deleted target
failures, repeated correspondence targets, and completed empty groups. Preserve
normal schema, CRS, ownership, and candidate-membership validation.

Verify honest validation and acquisition reporting, complete normalized requests,
and effective scopes through stored outputs, retained inputs, and exports.
Reopened retrieval, cached/fresh assembly, and simulated job expiry preserve
records and identities without fresh acquisition. Required persistence failures
follow the existing durable-success contract. Use isolated mocks and in-process
fakes under milestone 1 and the repository's service-access guard; tests do not
connect to databases, HTTP services, or other external resources.

## Deferred or excluded work

Transport representations, provider membership-check interfaces, and error names
will be specified after further discussion. The Python sketch establishes the
grouped table input without prescribing serialization or a physical
validation-storage layout.

Set merging, precedence rules, updates retaining an existing ID, replacement
helpers, automatic relationship repair, and new intervention kinds are outside
this version. Arbitrary table injection and caller-supplied dependency replacements
remain unsupported. Full row-level tracing, rejected-candidate histories, automatic
comparisons, and special result-fetching semantics are not prerequisites.

Interventions affect only Lyra-managed location access. Direct metric or resource
reads of external data retain their ordinary acquisition contract and see the
unchanged external source. Authors use the supplied effective location records
where their computation must reflect interventions. No query interception,
external-data mutation, or archive of the complete base dataset is introduced.

## Reference points

Build on [request ownership and handoff](04-plugin-discovery-and-normalized-jobs.md),
[provider identity and acquisition](05-location-providers-and-origins.md),
[shared execution](06-unified-graph-execution.md), and
[managed selection](07-operation-target-selection-with-ibis.md). Use the existing
[value store](08-sqlite-result-storage.md) and [cache rules](09-node-caching.md),
with [execution reports](10-execution-reports-and-durable-success.md),
[result retrieval](11-result-and-report-retrieval.md),
[retained inputs](12-retained-location-inputs.md), and
[GeoParquet exports](13-geoparquet-exports.md) for ordinary inspection.
