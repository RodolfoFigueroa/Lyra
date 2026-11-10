# 5. Datasets and external origins

Give location data a small, immutable identity contract and make direct external
acquisition explicit in ordinary declarations. Dataset identity describes the
data; a provider identifies the executable code retrieving it; an origin describes
a node's declared external data boundary.

## Accepted scope

### Dataset registrations and provider lookup

A dataset is a registered collection of location records. Each location contains
an ID, geometry, and attributes. One provider node can serve several registered
datasets and many requested subsets; there is no separate provider node or
dataset identity for each location or acquisition batch.

Replace separate provider `collections`, `outputs`, and `contracts` declarations
with one dataset registration list. Each registration contains a required stable,
unique dataset `id`, optional opaque `revision`, optional plain-text `description`,
and executable `schema`. Derive discovery and runtime lookups from these
registrations. Multiple datasets may share a schema while retaining distinct IDs.
Descriptions appear in discovery and reports without affecting identity, routing,
or cache keys.

Allow datasets from multiple active providers. Index each unique dataset ID to
its registration and owning provider, and acquire it through that provider's
ordinary resource lifecycle. Reject conflicting registrations before acquisition.
One provider may serve many datasets, and different providers may publish datasets
with the same schema. Schema compatibility neither chooses among providers nor
merges their dataset identities.

The dataset ID replaces compound family/source/edition identity. Separate
geographical editions, observation periods, releases, and field-definition
mappings are not descriptor fields. Concrete location bindings and operation
targets reference dataset IDs. Reusable inputs instead declare required schemas
and receive concrete dataset selections when the graph is bound, as specified
below and in milestone 6. Adapt routing and consumers together so existing execution
remains usable. Equal IDs in different datasets or revisions imply no geographic
equivalence; authors declare relationships explicitly. Losing a built-in
distinction between shared geography and separate observations is an accepted
simplification. Dataset constructor names and internal indexes remain choices for
implementation.

A provider is executable code retrieving members of a registered dataset. Its
exact-ID resolver receives only the dataset ID, revision, and requested string
location IDs, together with ordinary execution resources. That interface accepts
no arbitrary calculation parameters or upstream metric-table arguments. Parameters
such as a search radius belong to the selecting metric or operation.

[Milestone 7](07-operation-target-selection-with-ibis.md) adds predicate selection
through managed dataset adapters as a separate acquisition capability. Operations
construct lazy filters; Lyra executes them against the registered target dataset
and validates candidate records. This does not add computation parameters to the
exact-ID resolver or change a resolved record's identity.

For example, an operation may select hospital IDs using a building and radius,
but the hospital record for a fixed dataset/revision/ID does not depend on that
selection. Changing the operation's mapping does not change the hospital's
identity. A relationship field stored inside the hospital record, however, is
part of that record's immutable contents.

Each `(dataset_id, revision, location_id)` identifies one frozen record,
independently of provider implementation, resource configuration, acquisition
batch, or selection path. `revision=None` means fixed unversioned contents, not
changing latest data. Changed geometry, attributes, or relationship fields in a
record require a different declared identity. Dataset ID/revision also fixes the
complete searchable membership: adding or removing members requires a new dataset
ID or revision, including for fixed unversioned data. A changed location ID alone
does not authorize changing membership within the same dataset revision. Providers
return the requested revision and exactly one record for each successfully resolved
ID; missing records are acquisition errors. Authors own the identity guarantee.
Lyra validates structure without hashing or comparing contents to establish identity.

Providers return ordinary GeoDataFrames validated against the registered Pandera
schema. Dataset and revision come from the resolved registration and acquisition
request, not from `DataFrame.attrs`. The immutable identity guarantee concerns
registered record contents; it does not require frozen Python table objects.
[Milestone 6](06-unified-graph-execution.md) supplies independent working frames
to handlers while retaining authoritative records and scope in the runtime.

Exact-ID resolution can acquire only the records currently needed because each
record is independent of other requested IDs. Empty needed ID selections invoke
no resolver. Predicate selection discovers its matching set through the managed
adapter; individual resolved records do not establish that set's completeness.
Caller-selected IDs remain request context, and a computed selection remains a
graph dependency; neither becomes an extra exact-ID resolver argument or record
identity field. A revision identifies data without archiving a historical snapshot.

Provider schemas explicitly declare unique, non-null string `location_id` and
active `geometry`, including expected CRS and existing geometry validity
requirements. Authors rename external columns before return. Reserve
`location_id`, `source_location_id`, `row_key`, and `geometry` for their accepted
structural roles and reject collisions. CRS comes from the schema,
including for empty tables; different CRSs require schema variants rather than a
second dataset-level CRS setting. Public location IDs use Pydantic's strict string
validation without coercion or a custom identity codec; Pandera owns the declared
table-column checks. Authors expose numeric or composite source identifiers as
stable, unambiguous strings.

### Dataset schemas and consumer requirements

A dataset schema describes its complete records. A consumer's location input schema
describes the columns, supported types, nullability, and geometry/CRS requirements
needed by that computation. Use ordinary Pandera declarations for both roles;
required input columns may be a subset of a dataset's columns. Additional columns
remain available in the supplied GeoDataFrame. A schema is not a dataset identity,
provider selector, or registration of a semantic location family.

Register `census_tracts_1990` and `census_tracts_2000` separately even when they
share attributes. Their IDs and geometries may differ. A reusable census input
can require the common attributes without fixing either dataset. A polygon-only
input can also accept city blocks without requiring census attributes. Callers
select one registered dataset per named input, with its concrete revision resolved
from the registration. Dataset-specific inputs remain available where required.
Milestone 6 owns graph binding, input validation, and output ownership under these
declarations.

Authors guarantee that supplied attributes have the meanings their computations
expect. Matching column names or types does not prove that guarantee, geographic
equivalence, or temporal alignment. Choosing one dataset does not automatically
change other datasets or external origins used by the graph. Reusable input
contracts add no type hierarchy, schema-equivalence proof, or temporal selection
framework.

### Declared external boundaries

Provide an immutable, reusable `Origin` value with required stable `id`, optional
opaque `revision`, and optional plain-text description. It need not identify a
registered location dataset. Identity is `(id, revision)`, independent of Python
object identity; descriptions are informational. As with datasets, `revision=None`
identifies fixed unversioned contents. Changed external contents require a changed
declared identity or applicable effective key input.

Metrics declare every origin from which they directly acquire data. A node
consuming a source only through an upstream output needs no direct declaration
for it; a metric reading it directly must declare it even if an upstream dependency
exposes the same source. Operations compute from declared inputs and parameters;
their handlers and optional selectors acquire no external data directly and have
no resource bindings or direct-origin declarations. Location acquisition belongs
to providers and managed dataset adapters, with origins derived from the dataset.

Allow metric origins both on node declarations and existing `Resource(...)`
bindings. A resource annotation contributes the binding's origins to its consuming node.
The effective collection is their union, deduplicated by identity. An empty
collection promises no additional direct external acquisition. Explicit node
origins support acquisition outside managed resources as well.

For example, an author can reuse
`Origin(id="national-census", revision="2025")` in a
`Resource("census_db", origins=(census_origin,))` binding. Referencing that binding
in `Annotated[CensusDatabaseIO, census_access]` contributes the origin without
repeating it on the node. A binding may declare zero, one, or several origins.
Two metrics that directly query census data each declare it; a node consuming only
the other's census-derived output needs no direct census declaration.
The example illustrates the declaration contract; exact declaration spelling
remains an implementation choice.

Collect and validate these declarations during registration and plan preparation,
before cache lookup, resource initialization, credential resolution, or service
access. Discovery, computation keys, and reporting use the same collection as
those features become available. Resource names still govern configuration and
instance sharing: different bindings may describe different origins while
sharing one resource, without extra instances or lifecycle changes.
Separately constructed origins with equal `(id, revision)` are equivalent, and
repeating an origin at both declaration locations does not change identity.
Cached and skipped nodes keep their declarations without claiming acquisition.

Dataset registration automatically supplies the origin ID, revision, and
description for provider resolution and managed predicate acquisition. Keep
provider implementation identity separate. No additional origin registry or
independently managed provider-origin token is needed. Credentials, connection
settings, queries, file hashes, and
internal processing descriptions are not required origin metadata or extra key
inputs. Authors must represent output-affecting differences through accepted
identities or effective parameters.

Complete dependency and origin declarations are required for every output scope
and cache setting. Authors own accuracy; Lyra neither inspects handler code nor
intercepts queries to discover undeclared acquisition. Resource operation logs
remain diagnostics. Declarations do not prove which sources a handler actually
accessed, archive external data, or guarantee its later availability.

Provider implementation identity is its node ID and optional opaque code revision.
Reports retain that identity, but provider record keys and downstream location
inputs depend only on the dataset/revision/location tuple. A metric's effective
direct-origin IDs/revisions contribute to its computation key and propagate through
dependent computations. Operations have no direct origins; their declared target
dataset/revision and input dependencies capture the data used by selection and
mapping. Descriptions are excluded from these identities. Provider origin identity
is already represented by its dataset, so it needs no additional cache token.

### Native table construction and validation

Replace production `LocationPayload` calls carrying empty feature lists with
focused scope and CRS validation. Remove the unused feature-list validation path
while preserving required checks of actual location contents at shared
producer and persistence boundaries. Authors and test helpers construct ordinary
GeoDataFrames and declare Pandera schemas; validation returns native tables.
Keep required ID, geometry, CRS, type, and nullability checks at their designated
boundaries. Schema compilation does not require another public data container.

Replace the authoring compatibility-check workflow with representative native-table
authoring checks. Remove the supported-version gate and checks tied to internal
payload schemas or location factories. The checks exercise declarations, valid
and invalid producer frames, and normal execution without compatibility machinery.
Milestone 6 owns removal of the public location-value abstraction across execution,
construction, and inspection interfaces.

## Completion boundary

Registered datasets can be discovered and acquired through the new provider
contract, and their consumers, routing, and ordinary result paths still work.
Origin metadata is available without acquiring resources. Isolated fake providers
cover strict IDs, requested revisions, missing records, schema/CRS preservation,
and empty selections; declaration checks cover origin union, deduplication,
unchanged metric resource sharing, and rejection of operation resource bindings
and direct origins. Producer checks use native frames, preserve empty and all-null
schemas, and keep dataset identity independent of mutable frame metadata.

Cover datasets served by separate fake providers, multiple datasets served by one
provider, and conflicting dataset registrations. Verify provider lookup by dataset
identity even when registered schemas match. Discovery distinguishes complete
dataset schemas from consumer input requirements without acquiring data.

The next milestone unifies all managed request paths around these contracts.
Local development can use a registered provider over fixed local data; tests use
in-process providers and isolated records under the same identity guarantees.
The managed caller contract is parameters plus registered dataset/ID selections;
providers supply records, and declared upstream nodes supply dependency tables.
Consolidating all local, development, and remote entry paths around this contract
is the next milestone's runtime work. Full-record injection, arbitrary table
injection, and caller-supplied result references replacing dependencies are not
alternative acquisition contracts to preserve.

## Deferred or excluded work

Provider record caching is completed in [milestone 9](09-node-caching.md), and
observed acquisition/reuse/skipped reporting in
[milestone 10](10-execution-reports-and-durable-success.md). A declared provider
boundary alone must not be presented as an observed acquisition.

The provider reuse contract is boolean and off by default. For exact-ID resolution,
enabled caching reuses needed records individually and acquires only misses;
disabled caching acquires needed IDs without own-record reuse. Both allow reduced
ID selections, and a valid downstream hit may bypass acquisition entirely.
Shared inputs and upstream
computation scope still determine which records are needed. Implementing that
scheduling belongs to the caching milestone, without changing this lookup or
immutable-record contract. Predicate acquisition discovers complete membership and
is not satisfied by individually cached records; its separate execution boundary
is supplied in milestone 7.

There is no separate origin registry, query interception, per-call source
tracking, content-based identity verification, or proof of external-I/O behavior.
Locations constructed externally can be exposed through registered datasets;
creating and registering new locations inside operations is outside scope.

## Reference points

Dataset work spans [provider declarations](../src/lyra/sdk/locations.py),
[provider lookup](../src/lyra/runtime/planning/providers.py),
[dataset contracts](../src/lyra/contracts/locations.py), and
[geometry schemas](../src/lyra/sdk/location_schema.py); the
[city provider](../examples/city-plugin/city_example/provider.py) is an authoring example.
Validation and authoring-check work also involves the
[payload model](../src/lyra/data/location_payload.py),
[location construction](../src/lyra/data/locations.py), and
[authoring-check script](../skills/lyra-metric/scripts/check_compatibility.py).
