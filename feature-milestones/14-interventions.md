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

Omitting interventions or supplying an empty collection means ordinary execution.
Every supplied set must contain at least one addition or removal; reject sets
whose two actions are both omitted or empty. Reject repeated removal IDs within
a set, just as duplicate added IDs fail. Valid contents remain order-independent.

A target catalog identifies one registered location provider by its public
provider ID. It does not identify a schema, resource configuration key, or a new
registry. An unknown catalog fails validation. Resolve and retain the provider's
base scope, including its code revision and declared origin identities, through
the existing preparation and worker-handoff contracts.

A provider is targeted when a supplied intervention set names its catalog,
regardless of whether the execution graph reads that catalog or selects any of
its records. During request preparation, resolve every target and verify that
its provider supports the membership lookup specified below. Unsupported targets
fail clearly before resource acquisition or computation. Registration and ordinary
execution do not require that capability for providers targeted by no set.

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
validation. Use the provider's `existing_ids()` lookup independently of the
selected subset; strict exact-ID resolution still fails on missing requested
records. Do not require a full dataset copy for intervention validation. Checking
lookup support during preparation performs no acquisition and does not invoke
the lookup itself.

Establish validity for every supplied set before downstream computation receives
intervened inputs or the run accepts a cached result as successful. Validate even
interventions that the requested selections would exclude or that skipped work
would never use. Empty selections and cache hits do not waive these checks.
Any invalid intervention fails the request as a whole; consumers do not receive a
partially accepted collection of sets. Provider and adapter failures remain
failures rather than evidence that a record is absent.

Persist a small internal record of successful validation in the existing store
for each completely validated set. Key it by the full base provider scope, including
provider ID, code revision, and origin identities, together with the set's
`(catalog, id, revision)`. Publish the record only after all validation for that
set succeeds; failed or partial checks do not create a reusable success record.
Use the existing store's persistence and error-handling conventions.

On subsequent executions, reuse a matching record to skip the base-membership
lookup. Every submission still validates request structure, target registrations,
lookup capability, and addition schemas before reuse. A changed base scope or set
identity requires validation under that new key. Reuse relies on the existing
immutability promises and introduces no content hashes or caller-facing controls.
A computation cache hit or retained location records alone do not establish
complete intervention validity. Validation failures follow ordinary execution
error paths and do not publish successful results or reports.

### Managed effective datasets and staging

Use request-owned temporary staging for intervention-aware predicate execution in
both managed adapters. On the first needed selection for an effective catalog,
load its validated additions and removal IDs once and reuse that preparation for
later selections in the same execution scope, including different operations and
physical batches. Keep staging lazy: complete membership validation uses the
ordinary base lookup independently of whether candidate selection is needed.
Exact-ID acquisition may combine accepted additions with ordinary base lookups
while enforcing the same effective membership and strict missing-ID behavior.

The internal query combines base rows excluding removal IDs with complete added
rows, then applies the validated author selector. Execute both branches through
the same supported backend, preserving its spatial, attribute, and null semantics.
Carry the registered schema through staging and result decoding, including typed
empty additions and all-null attributes. Adapters own relation binding and
backend-specific literal emission under milestone 7; authors keep the existing
single-provider selector API and restrictions. Preparing this relation does not
require copying the complete base dataset into staging.

PostGIS staging uses the provider's borrowed `PostgresIO` backend and requires
the database `TEMPORARY` privilege alongside ordinary base read access. Missing
staging privileges fail the acquisition that needs them. Registration and discovery
remain declaration-only; ordinary requests and standalone membership checks do
not require temporary-table creation. Staging does not require permission to
mutate base relations or create persistent database objects. DuckDB staging follows
the provider-owned embedded backend lifecycle.

Give temporary objects distinct ownership per request and effective catalog,
including when multiple catalogs share a backend or different requests reuse bare
added IDs. Choose names that do not shadow existing relations and qualify provider
base references. Reuse staging only within its owning execution scope; physical table
names and connection handles do not participate in durable dataset or cache
identities. Forward selected authoritative records without staging or applying the
same intervention again for each correspondence.

Drop owned staging objects before their backend owner closes, after success,
failure, or cancellation, including partial staging setup. Clean up only the
objects belonging to that preparation without closing borrowed backends. Follow
milestone 5's cleanup-error rules: attempt remaining cleanup, preserve an original
execution failure, and fail an otherwise successful run if cleanup fails. Use
explicit managed cleanup rather than relying on garbage collection or eventual
connection closure. Resource use remains sequential within an execution; staging
introduces no transaction spanning the run.

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
Caller-supplied additions do not count as external record acquisition; loading
them into temporary staging is internal preparation. Reusing staging does not
suppress reporting of base reads or candidate queries that actually run. Reusing
established validation must not claim a fresh membership lookup. The existing
successful report and durable-publication contract remains authoritative.

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

### MCP workflow and client responsibilities

Assume agents using Lyra's MCP server can read and write files in their own
environment. Code execution, package installation, Arrow readers or writers, and
geospatial tooling are optional client capabilities. Basic intervention submission
and bounded result inspection must remain usable through ordinary JSON tool calls.
Filesystem access alone does not establish a shared filesystem with the server
or the ability to interpret binary table files.

Lyra exposes registered metrics and providers, their schema descriptions, units,
and CRS through ordinary discovery. It owns authoritative request validation,
execution, and existing result, report, and retained-input retrieval. Agents select
the metric and catalog, prepare complete addition records and removal IDs, manage
set identities and revisions, and coordinate and compare baseline and intervention
runs. Required values remain the caller's responsibility; natural-language intent
or coordinates alone do not supply missing schema attributes.

Agents obtain missing source data and perform geocoding, reprojection, or complex
geometry construction through their available tools or user input. Existing
retained root inputs may supply records for externally prepared replacements,
subject to milestone 12's selection and availability rules. They do not provide
arbitrary catalog browsing. Access to other source records remains external to
this milestone.

## API sketch

### Python submission

Use `run(metric, *, inputs=None, parameters=None, interventions=None)`, extending
[milestone 4's submission contract](04-plugin-discovery-and-normalized-jobs.md#shared-submission-contract).
Local `run()` and `run_development()` and both client modes' submission methods
share these request keywords. Python `None` means omission; `inputs` and
`parameters` are separate mappings, and metric-specific requirements still apply
when either is omitted. Each `Intervention` represents the complete change set
for one catalog. Its `id`, `catalog`, and `revision` retain the immutable set identity described
above. Additions are grouped in one ordinary GeoDataFrame; removals are a list
of strict string location IDs.

For this illustration, assume a registered `tree_coverage` metric accepts a
`blocks` selection and a `radius_m` parameter. Its `trees` provider declares exactly
three columns: string `location_id`, point `geometry` in EPSG:4326, and nullable
float `height_m`. The GeoDataFrame `df_additions` satisfies that schema and contains
the two records shown in the corresponding JSON example below:

```python
run(
    "tree_coverage",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["block_1"]}},
    parameters={"radius_m": 100},
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
means no removals, but the set must still contain at least one change. A
removal-only set requires no empty GeoDataFrame. An explicitly supplied empty
additions frame must still satisfy the provider's complete schema,
including its declared columns, active geometry, and CRS, and the set must include
at least one removal.

The additions frame uses the provider's `location_id`, `geometry`, and attribute
columns. Its index and row order have no semantic significance; IDs come from
`location_id`, and duplicate added IDs fail validation. Removal-list order is
likewise insignificant, and repeated removal IDs fail validation. Authors can
construct and concatenate ordinary frames without per-record intervention objects
or a custom table wrapper.

Authors may validate the additions frame themselves using the provider's existing
Pandera schema before submission. Lyra still performs authoritative validation
against that schema, separately from the base-dataset checks establishing that
added IDs are absent and removed IDs exist. Submission captures independent frame
and removal-list values, so later caller mutations cannot change the accepted
request.

### Inline JSON transport

Extend the shared HTTP body and MCP tool arguments object with optional top-level
`interventions`. The complete request fields are `metric`, `inputs`, `parameters`,
and `interventions`. Omitted `inputs` and `parameters` follow milestone 4's empty
mapping and validation rules. Omitted `interventions` means an empty array.
Reject explicit JSON null for these request fields and unknown envelope fields;
schema-permitted null values inside parameters and addition records remain valid.

Each intervention is an object with required `id`, `catalog`, and `revision`
strings. Its optional `add` field is directly an array of record objects, and
`remove` is an array of strict string IDs. An omitted action means an empty array;
explicit null actions and unknown intervention fields are invalid. Reject a set
with no changes or repeated removal IDs. The registered provider supplies record
types, active geometry, and CRS without caller-supplied column or schema metadata.

The Python example above corresponds to this HTTP request body or MCP tool
arguments object:

```json
{
  "metric": "tree_coverage",
  "inputs": {
    "blocks": {
      "provider": "city_blocks",
      "ids": ["block_1"]
    }
  },
  "parameters": {
    "radius_m": 100
  },
  "interventions": [
    {
      "id": "planting-proposal-a",
      "catalog": "trees",
      "revision": "v1",
      "add": [
        {
          "location_id": "intervention_tree_2",
          "geometry": "POINT (15 15)",
          "height_m": 2.5
        },
        {
          "location_id": "intervention_tree_1",
          "geometry": "POINT (15 16)",
          "height_m": null
        }
      ],
      "remove": ["tree_1", "tree_2"]
    }
  ]
}
```

Carry additions directly in the request as inline JSON records through HTTP,
MCP, and the API-to-worker handoff. Remote Python clients accept the same
GeoDataFrame input as local callers and handle its serialization. HTTP and MCP
callers supply the corresponding JSON representation. Preserve the accepted set
identity, removal IDs, and complete additions table through normalization and
handoff without truncation or references to separately uploaded tables.

Use the registered provider schema to interpret these records, without requiring
callers to repeat its column types or introducing a general-purpose table schema
format. The request is a Lyra API payload using existing cell encodings, not a
new standard for serializing arbitrary DataFrames. HTTP and MCP share this JSON
input; Arrow encoding and base64 construction are not required client capabilities.

Reuse [milestone 11's JSON cell encoding](11-result-and-report-retrieval.md#json-cell-encoding)
for addition rows. Geometry uses full-precision WKT in the declared CRS; signed
64-bit integers use decimal strings for every non-null value. Strings preserve
their exact spelling, finite floats use JSON numbers preserving their value,
booleans use JSON booleans, and missing values use null. The registered schema
determines logical types, so numeric-looking location IDs remain strings.

Every JSON record includes every declared column, including nullable columns.
The provider schema supplies the logical types and active geometry needed to
construct empty tables and all-null columns. Geometry values use the provider's
declared CRS. Validate a submitted GeoDataFrame's columns, active geometry, types,
and CRS before serialization, including for an empty frame; encoding must not
erase a schema mismatch. The frame's index does not become a data column or
location identity.

Reconstruct native table values using the registered provider's logical schema
and validate decoded contents at the receiving boundary. Preserve values and CRS
without silently reprojecting geometry, repairing missing record fields, or
discarding unexpected fields. The provider schema remains authoritative for
geometry validity and nullability; a representable JSON cell is not necessarily a valid
location value. Retain the accepted additions with the resolved schema and CRS in
the report's normalized request so later inspection does not require the current
provider registration.

### Provider membership lookup

Providers supporting interventions implement this bulk lookup:

```python
def existing_ids(self, ids: list[str]) -> set[str]:
    ...
```

Return exactly the requested IDs present in the provider's base dataset. Missing
IDs are absent from the returned set; they do not make this lookup fail. Returned
IDs must be strict strings from the supplied ID list. The result
contains no geometry or attributes. Membership uses the same base scope and
declared origins as `resolve()`, independently of interventions and current
selections. Acquisition failures propagate as errors and must not be interpreted
as missing records. Ordinary `resolve()` retains its strict missing-ID behavior.

Lyra submits the deduplicated union of added and removed IDs in one logical bulk
lookup per targeted provider when base-membership validation is needed. No added
ID may appear in the returned set, and every removed ID must appear. This validates
the supplied set against the base dataset without requiring complete location
records or a copy of the entire catalog. Previously established complete
validation is reused through the persisted success record described above. A
matching record avoids initializing provider resources solely to repeat this
membership lookup; any other required acquisition retains its normal lifecycle.

Lyra supplies the implementation in the built-in Parquet and PostGIS provider
adapters. Authors using those classes inherit it without writing another lookup.
Custom providers implement the method to support interventions and may share an
implementation through inheritance. Providers without an implementation remain
usable for ordinary execution; requests targeting them with intervention sets
fail the capability check during preparation.

The lookup runs afterward through the provider's existing resources and normal
execution lifecycle. A target catalog unused by the computation still requires
support and complete intervention validation. Using a provider only as an ordinary
input or operation target, without an intervention set naming it, does not require
the membership capability.

## Completion boundary

Local, development, and remote execution support the same intervention semantics
through their shared runtime and request contracts. Verify consistent behavior
through synchronous and asynchronous Python clients, HTTP, and MCP, including
accepted-request ownership and worker transport. Requests without interventions
retain their existing behavior.

Cover omitted interventions, Python `None`, and an empty overall list as ordinary
execution. Reject explicit null JSON request sections, unknown envelope or set
fields, null actions, empty individual sets, duplicate added IDs, and repeated
removal IDs. Verify omitted and explicitly empty actions in otherwise nonempty
sets. Check both schema-valid and invalid empty additions frames when removals
are present; serialization must not conceal an invalid frame. Preserve index and
row-order independence. Caller mutations to submitted frames and removal lists
must not change accepted requests or results.

Verify inline transport through Python clients, HTTP, MCP, worker handoff, and
retained requests. Preserve the full signed 64-bit integer range, numeric-looking
string IDs, finite float values, full geometry precision, nulls, column information,
and CRS. Empty and all-null attribute columns retain their declared logical types.
Reject malformed encodings and schema or CRS mismatches without repairing or
truncating the supplied table. Validate Python frame metadata before serialization
and JSON record completeness on receipt. Exercise JSON submission and bounded
inspection without client code execution or an Arrow library. No upload service
is required for submission.

Verify schema validation, complete base-dataset collision and existence checks,
unknown catalogs, conflicting catalog sets, and failures outside selected data.
Check that invalid interventions fail before downstream computation or successful
cache reuse, including empty and skipped execution paths. Verify reusable complete
validation only under unchanged base and set identities. Failures of acquisition
must not become successful absence checks or partially accepted requests.

Verify persistence and reuse of successful validation across executions after
reopening the store. A matching record skips membership lookup, including when
computation is cached or the targeted catalog is unused. Changes to the provider,
its code or origin identities, or the set identity require fresh validation.
Request, capability, and schema checks still run when reusing validation.
Failed and partial validation never publish reusable success records; retained
computation or location values cannot substitute for those records. Exercise
failures to persist validation records through the existing store error paths.

Verify inherited membership support in both built-in adapters and custom provider
implementations, with membership evaluated against the full base dataset. Providers
without the capability remain registerable and usable for ordinary execution.
Intervention requests targeting them fail before resource acquisition, including
when the targeted catalog is unused by the computation. Check the combined ID
lookup, present and absent results, invalid returned IDs, and propagated lookup
failures using isolated provider fakes.

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

With isolated backend and resource fakes, verify lazy staging once per effective
catalog, reuse across repeated and different selectors, operation chains, and
physical batches. Validate unused catalogs and cached or skipped work without
creating unnecessary staging. Cover addition-only and removal-only sets, typed
empty and all-null staging columns, precise geometry and scalar values, and filters
that exclude added records. Confirm that complete selector predicates execute
against the effective relation before candidate materialization.

Check PostgreSQL privilege failures, base reads without staging privileges, and
the absence of base mutation or persistent-object creation. Exercise distinct
catalogs sharing a backend, successive requests, and separate request sessions
holding different values for the same added ID. Verify schema-qualified base
access and unchanged direct reads. Inject failures during staging setup, query
execution, and cleanup; cover cancellation, removal of owned objects, continued
cleanup after a failure, preservation of the primary error, and continued use of
borrowed resources until their owner closes them. Retain the ordinary resource
ownership and successful-publication rules.

Verify honest validation and acquisition reporting, complete normalized requests,
and effective scopes through stored outputs, retained inputs, and exports.
Reopened retrieval, cached/fresh assembly, and simulated job expiry preserve
records and identities without fresh acquisition. Required persistence failures
follow the existing durable-success contract. Use isolated mocks and in-process
fakes under milestone 1 and the repository's service-access guard; tests do not
connect to databases, HTTP services, or other external resources.

## Deferred or excluded work

The Python keywords and JSON envelope above establish the submission contract.
Detailed errors follow existing framework validation and domain-error conventions;
the physical table layout for persisted validation records remains an
implementation detail within the existing store.
An alternative backend strategy using query-local SQL values, automatic strategy
selection, and caller-facing staging controls are outside this version.
Arrow input, separate bulk uploads, uploaded-table references, server reads of
agent-local paths, and upload lifecycle management are outside this version.

Revisit richer conversational MCP workflows after the underlying execution and
retrieval systems are established. This milestone adds no scenario orchestration,
agent runtime, file-management tools, geocoding or geometry-editing service, or
fresh provider-browsing API. Existing Arrow downloads and GeoParquet exports remain
available to clients with the tools to use them.

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

Feasibility evidence is preserved on branch `experiment/interventions-feasibility`
at commit `7ead885`, under `experiments/interventions/`. Its findings, locked
environment, and recorded runs support the staging choice and identify adapter
compatibility work. Verify the chosen library combination during implementation;
the experiment's synthetic workloads establish neither product size limits nor
latency guarantees.
