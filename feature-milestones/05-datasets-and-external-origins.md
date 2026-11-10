# 5. Datasets and external origins

Give location data a small, immutable identity contract and make direct external
acquisition explicit in ordinary declarations. This milestone covers decisions
2, 19, and 20 and R5 in [the ledger](../DECISIONS.md), with the provider-specific
identity and validation constraints of decisions 3 and 6 and R4/R7.

## Accepted scope

### Dataset registrations and provider lookup

Replace separate provider `collections`, `outputs`, and `contracts` declarations
with one dataset registration list. Each registration contains a required stable,
unique dataset `id`, optional opaque `revision`, optional plain-text `description`,
and executable `schema`. Derive discovery and runtime lookups from these
registrations. Multiple datasets may share a schema while retaining distinct IDs.
Descriptions appear in discovery and reports without affecting identity, routing,
or cache keys.

The dataset ID replaces compound family/source/edition identity. Separate
geographical editions, observation periods, releases, and field-definition
mappings are not descriptor fields. Metric inputs and operations reference dataset
IDs, with routing and consumers adapted together so existing execution remains
usable. Equal IDs in different datasets or revisions imply no geographic
equivalence; authors declare relationships explicitly.

A provider is executable code retrieving requested members of a registered
dataset. It receives only the dataset ID, revision, and requested string location
IDs, together with ordinary execution resources. It accepts no arbitrary
calculation parameters or upstream metric-table arguments. Parameters such as a
search radius belong to the selecting metric or operation.

Each `(dataset_id, revision, location_id)` identifies one frozen record,
independently of provider implementation, resource configuration, acquisition
batch, or selection path. `revision=None` means fixed unversioned contents, not
changing latest data. Changed geometry, attributes, or relationship fields in a
record require a different declared identity. Providers return the requested
revision and exactly one record for each successfully resolved ID; missing records
are acquisition errors. Authors own the identity guarantee. Lyra validates
structure without hashing or comparing contents to establish identity.

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

### Declared external boundaries

Provide an immutable, reusable `Origin` value with required stable `id`, optional
opaque `revision`, and optional description. It need not identify a registered
location dataset. Identity is `(id, revision)`, independent of Python object
identity; descriptions are informational. As with datasets, `revision=None`
identifies fixed unversioned contents. Changed external contents require a changed
declared identity or applicable effective key input.

Metrics and operations declare every origin from which they directly acquire
data. A node consuming a source only through an upstream output needs no direct
declaration for it; a node reading it directly must declare it even if an upstream
dependency exposes the same source.

Allow origins both on node declarations and existing `Resource(...)` bindings.
A resource annotation contributes the binding's origins to its consuming node.
The effective collection is their union, deduplicated by identity. An empty
collection promises no additional direct external acquisition. Explicit node
origins support acquisition outside managed resources as well.

Collect and validate these declarations during registration and plan preparation,
before cache lookup, resource initialization, credential resolution, or service
access. Discovery, computation keys, and reporting use the same collection as
those features become available. Resource names still govern configuration and
instance sharing: different bindings may describe different origins while
sharing one resource, without extra instances or lifecycle changes.

Dataset registration automatically supplies a provider's origin ID, revision,
and description for the requested dataset. Keep provider implementation identity
separate. No additional origin registry or independently managed provider-origin
token is needed. Credentials, connection settings, queries, file hashes, and
internal processing descriptions are not required origin metadata or extra key
inputs. Authors must represent output-affecting differences through accepted
identities or effective parameters.

Complete dependency and origin declarations are required for every output scope
and cache setting. Authors own accuracy; Lyra neither inspects handler code nor
intercepts queries to discover undeclared acquisition. Resource operation logs
remain diagnostics. Declarations do not prove which sources a handler actually
accessed, archive external data, or guarantee its later availability.

### Location construction and authoring compatibility

Replace production `LocationPayload` calls carrying empty feature lists with
focused scope and CRS validation. Remove the unused feature-list validation path
while preserving required checks of actual location contents at shared
construction and persistence boundaries. Test helpers construct locations through
supported public APIs.

Reduce the authoring compatibility checker to a supported-version check and a
representative public authoring workflow, including GeoDataFrame-to-`Locations`
construction. Remove checks tied to internal payload schemas. Less exhaustive
compatibility diagnosis is acceptable; ordinary construction and required
validation remain supported.

## Completion boundary

Registered datasets can be discovered and acquired through the new provider
contract, and their consumers, routing, and ordinary result paths still work.
Origin metadata is available without acquiring resources. Isolated fake providers
cover strict IDs, requested revisions, missing records, schema/CRS preservation,
and empty selections; declaration checks cover origin union, deduplication, and
unchanged resource sharing.

The next milestone unifies all managed request paths around these contracts.
Local development can use a registered provider over fixed local data; tests use
in-process providers and isolated records under the same identity guarantees.

## Deferred or excluded work

Provider record caching is completed in [milestone 8](08-node-caching.md), and
observed acquisition/reuse/skipped reporting in
[milestone 9](09-execution-reports-and-durable-success.md). A declared provider
boundary alone must not be presented as an observed acquisition.

There is no separate origin registry, query interception, per-call source
tracking, content-based identity verification, or proof of external-I/O behavior.
Locations constructed externally can be exposed through registered datasets;
creating and registering new locations inside operations is outside scope.
