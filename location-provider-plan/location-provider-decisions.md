# Location providers

Extract location resolution into a plugin capability, with requests selecting
locations by unique IDs. These are design decisions for a planned feature;
they do not imply implementation.

A location is broadly any spatial entity that a plugin developer chooses to
identify and resolve: a census unit, parcel, building, road segment, monitoring
station, ecological area, or another domain-specific feature. Plugin developers
ultimately define what locations mean, how they are grouped into collections
and unit types, and which geometries represent them. Lyra supplies the common
ID-resolution and geometry-input contract.

## Accepted

- **ID-based requests:** callers select units through a list of opaque string
  IDs, scoped by a namespaced collection and a provider-defined unit type.
  Core does not infer meaning from an ID's length or format. Caller-supplied
  geometries are not accepted as spatial inputs.
- **Named location inputs:** metrics may declare multiple named location
  arguments, such as `blocks: LocationInput` and `lots: LocationInput`. Each
  argument uses the same resolution mechanism. There is no special `bounds`
  argument or separate bounds input kind.
- **One unit type per input:** each named location input selects IDs from one
  collection and one unit type. Different inputs on the same metric may select
  different collections or unit types through the deployment's single provider.
- **ID-list behavior:** deduplicate each input's IDs while preserving their
  first-occurrence order. Ordering has no calculation meaning; results are
  associated with IDs. Reject a request if any location input is empty or
  contains unknown IDs or IDs of the wrong type, identifying the offending
  input and IDs. Do not silently return a smaller selection.
- **Dependency forwarding:** automatically forward location inputs and ordinary
  parameter fields when their names match the producer's requirements. Explicit
  bindings take precedence; ordinary parameters may fall back to the producer's
  defaults. Report a missing required input when none of these rules supplies
  it. Do not guess location mappings from their shared `LocationInput` type.
- **Dependency bindings:** support explicit mappings when names differ or a
  consumer needs to override a forwarded value. For example, a block
  population metric can bind its `lots` input to the lot-population producer's
  `location` argument while using `blocks` to select its own results. Bindings
  forward resolved selections together with their collection and unit-type
  scope; they do not transform them or discover new IDs. Each producer's
  invocation and cache identity use its effective bound inputs under its own
  argument names. Unrelated consumer inputs do not affect that identity.
- **Scoped execution identity:** retain each resolved input's collection and
  unit type as internal execution metadata. Include that scope in invocation
  and cache identities alongside the resolved feature collection, including
  IDs, geometries, properties, and CRS, and the other calculation inputs.
  Metric handlers receive the agreed `LocationInput` without an additional
  identity wrapper.
- **Calculation context:** the caller supplies each declared ID list. The
  application and plugin own relationships and completeness requirements, such
  as supplying every constituent lot for a block total. Metrics may also use
  shared producers to acquire supporting data internally. Those producers may
  omit location arguments.
- **Exact resolution:** resolve only the explicitly supplied unit IDs. There is
  no region selection, expansion into constituent units, name search, or fuzzy
  matching. A municipality ID identifies that municipality itself.
- **Resolution timing:** validate all named selections, acquire provider
  resources, and resolve each selection once before accepting and enqueueing
  a job. Unknown IDs reject the request immediately. The queued job carries
  the resolved features and their internal scope metadata; workers do not
  repeat location resolution. Submission waits for resolution to finish.
  API, MCP, and Python-client submissions use the same server-side resolution
  path. Local execution invokes the same resolver before running the graph.
- **Single provider:** the first implementation assumes one configured location
  provider per deployment. Requests do not select or route between providers.
- **Provider ownership:** plugins define their location collections, identifier
  rules, unit types, and authoritative resolution. Lyra's core does not prescribe
  census levels, identifier lengths, database tables, or a national CRS.
- **Geography separation:** dataset-specific identifier validation and geometry
  acquisition belong to independent provider projects. Lyra has no dependency
  on a particular geography implementation; see the phase 12 handoff for the
  repository boundary.
- **Stable geometry:** within a collection and unit type, the same ID always
  identifies the same geometry. The first implementation does not need geometry
  revisions, snapshots, or mutable-location cache invalidation. Freshness of
  other calculation data remains a separate concern.
- **Metric representation:** retain the existing `LocationInput` GeoJSON-style
  model. Each named input receives a feature collection of provider-resolved
  units. Each feature has a string ID, geometry, and a properties dictionary,
  which may be empty. Collection and unit-type information stays internal to
  selection, resolution, and execution without adding an identity envelope
  to handler inputs. Callers continue to submit only ID selections.
- **Geometry and CRS:** support points, lines, polygons, and their multipart
  variants. Each collection declares its CRS; metrics handle any reprojection
  their calculations require.
- **GeoDataFrame conversion:** metrics may use the existing
  `convert_geojson_to_gdf()` helper, which preserves feature IDs as the index
  and carries over the CRS. Keeping the GeoJSON-style handler contract preserves
  the existing validation, serialization, forwarding, and invocation-identity
  machinery while authors can use GeoPandas for calculations.
- **Metric suitability:** keep structural validation and leave
  calculation-specific suitability to plugin authors. An exhaustive framework
  of collection, unit-type, and metric compatibility declarations is outside
  the first implementation.
- **Resources and interfaces:** providers own acquisition through managed
  resources; PostgreSQL is one possible source. Providers are registered through
  plugin declarations, with core supplying the common interfaces. Request
  validation and exact-ID resolution work consistently through the API, Python
  client, MCP, and local execution. A plugin may provide locations without metrics.
- **Scope:** provider registration, ID resolution, and explicit dependency
  bindings work with the existing whole-invocation executor and do not require
  per-unit batching or caching. Automatic discovery of related units is outside
  this feature. Scenario management stays in the application.

## Metric authoring direction

Keep metrics as ordinary decorated functions whose signatures declare named
`LocationInput` arguments, a parameter model, and upstream result arguments.
Dependency declarations identify upstream outputs; optional bindings select
which consumer inputs supply the producer's arguments. Authors should not need
to repeat the upstream contract or write bindings for matching names. A location
input may be declared solely to forward it to a dependency.

Prefer extending the existing `@metric`, `Dependency`, and `Input` interfaces.
The exact declaration and reference syntax may evolve during implementation
while preserving the forwarding and binding behavior above.

- **Input descriptions:** authors document what each named location input
  represents, including expected units, geometry kinds, and any required feature
  properties, such as `parent_block_id`. Make these descriptions available
  through plugin inspection. Keep them descriptive; annotation and metadata
  syntax may evolve without introducing a compatibility-validation framework.
- **Read-only locations:** treat resolved `LocationInput` values as read-only.
  Multiple nodes may share a selection, and its contents participate in cache
  identity. Authors copy features before modifying them or work with the
  GeoDataFrame produced by the conversion helper. This is an authoring contract
  for the first implementation; elaborate immutability enforcement is not
  required.
- **Testing:** unit tests may call handlers directly with constructed
  `LocationInput` fixtures. Tests exercising full graph execution submit ID
  selections and use fake providers through resource overrides. Execution
  requests remain ID-only, including in local execution. The exact test-helper
  interface may evolve alongside resource integration.

## Provider authoring direction

Prefer an SDK base class, tentatively `LocationProvider`, that builds on the
existing `BaseIO` resource model. Keep the interface small and centered on a
batch-resolution operation. The exact inheritance, method signatures, metadata
representation, registration syntax, and error types may evolve during
implementation while preserving the accepted behavior above.

- **Resource integration:** reuse configuration models, credential handling,
  setup, cleanup, and operation logging. Authors implement acquisition using
  the resource mechanisms appropriate to their data source.
- **Collection declarations:** expose supported collections and unit types as
  metadata available during plugin inspection without service access. Unit
  types are plugin-defined categories and need not form a geographic hierarchy.
- **Batch resolution:** an operation such as
  `resolve(collection, unit_type, ids)` fetches the explicitly requested units
  and returns the agreed feature collection. The provider interprets IDs and
  supplies geometries, properties, and CRS.
- **Validation and errors:** Lyra validates declared collections and unit types,
  rejects empty selections, deduplicates IDs, and checks that returned features
  match the requested IDs exactly, without missing, duplicate, or extra units.
  Providers report unresolved IDs through a shared SDK error contract, including
  when none resolve. Invalid selections remain distinguishable from acquisition
  failures.
- **Registration:** extend plugin declarations to designate the location
  provider, preferably by referencing a registered named resource and reusing
  its deployment configuration. Support plugins that provide locations without
  declaring metric nodes.
- **Lifecycle:** initialize the provider once in a resource scope shared by all
  named selections for a submission, and close it before enqueueing. Local
  execution uses the same lifecycle before running the graph. Prefer synchronous
  resolution, consistent with existing I/O resources; the server runs blocking
  acquisition outside its event loop.
