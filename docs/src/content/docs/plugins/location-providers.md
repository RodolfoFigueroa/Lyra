---
title: Define a location provider
description: Declare a native record schema and resolve exact IDs through a named resource.
contentType: Reference
contentPlan: plugins/location-providers
sidebar: { label: "Define A Location Provider" }
---

A location provider exposes one collection of records under a public provider ID. Choose a managed provider below or implement `LocationProvider`, and register it as a named resource. The [blocks and lots tutorial](../location-example/) demonstrates the complete contract without services.

## Declare a provider

Define these class attributes:

| Attribute | Contract |
| --- | --- |
| `id` | Stable public identifier, such as `transport_stations` |
| `schema` | One executable Pandera `DataFrameSchema` or `DataFrameModel` |
| `code_revision` | Optional opaque revision for retrieval, transformation, schema, and membership logic |
| `origins` | Direct external dependencies as a sequence of `Origin` values; defaults to empty |
| `description` | Optional plain-text description |
| `cache` | Boolean record-reuse policy; defaults to `False` |

Import `LocationProvider` and `Origin` from `lyra.sdk`. An origin identifies an external data source through a stable `id` and an optional `revision`. Add an optional `description` to explain that source.

Origin identity is `(id, revision)`. Descriptions, declaration order, and repeated equivalent declarations do not affect that identity. Declare `origins=()` only when the provider reads no external data directly.

Each provider defines one collection. Use separate providers for blocks and streets, even when both share an origin. Providers can share a schema without sharing record identity. Fixed releases exposed simultaneously need distinct provider IDs.

Declare metadata without creating clients or reading secrets. Registration and catalog compilation validate declarations without acquiring resources.

### Reuse records by ID

Set `cache = True` to reuse saved records by exact ID. Only booleans are valid; Lyra rejects strings and integers before acquiring resources. With either setting, Lyra acquires only the IDs that the computation needs.

With caching enabled, Lyra also skips acquisition for records it has already saved. Predicate queries still retrieve every matching record because saved individual records don’t establish query membership. See [cache policies](../../concepts/caching/#choose-a-cache-policy) for the relationship between provider and operation reuse.

## Declare the native schema

Set `schema` to a Pandera schema with `strict=True` and these required columns:

- `location_id`: unique, non-null, nonempty strings
- `geometry`: the active geometry column, with the expected CRS and geometry validity checks
- Every attribute the provider returns

Include descriptions and unit metadata for all columns; use `None` when no unit applies. Attributes may contain nulls when their declarations allow them, but every declared column must be present. Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for structural fields.

Schemas cannot use these features:

- Optional columns or regex column names
- Filtering or row dropping
- Automatic insertion of missing columns

Lyra validates returned frames against the registered schema, including frames from fake resource overrides. Validation rejects incorrect columns, invalid geometry, and incorrect CRS. Lyra applies declared coercion before passing records to handlers. Rename external columns and reproject coordinates before returning them.

Empty selections and operation results retain the declared columns, dtypes, active geometry, and CRS. Location IDs are opaque strings; expose numeric or composite source IDs as stable, unambiguous strings.

## Resolve a batch

For a custom `LocationProvider`, implement synchronous `resolve(self, ids: list[str]) -> gpd.GeoDataFrame`. Lyra passes nonempty, deduplicated IDs without guaranteeing their order. Return exactly one complete record for each requested ID, preserving its spelling. Keep each record’s contents independent of the other requested IDs.

Lyra validates the frame and rejects missing, duplicate, or extra records. Align records by `location_id` because selection and retrieval don’t guarantee row order. Lyra skips the resolver for empty selections.

Calling `resolve()` directly skips the runtime’s validation. Test that validation through the runner with an in-process provider.

Raise `UnresolvedLocationIDsError(self.id, "location", missing_ids)` for unknown IDs, including when none resolve. Raise `LocationAcquisitionError` for service failures, chaining the cause without including credentials. The shared runtime adds node and input context. Invalid returned frames raise `LocationProviderContractError`.

## Look up base membership

To support [interventions](../../use/spatial-inputs/#prepare-changes-for-a-catalog), implement the synchronous method `existing_ids(self, ids: list[str]) -> set[str]`. Return the requested IDs that exist in the base dataset as a set of strings. Omit missing IDs and propagate resource or query errors.

Check the entire dataset under the same [provider scope](#define-revision-coverage) as `resolve()`, regardless of the current selection. Ordinary requests can use providers without this method. For an intervention request, Lyra checks each target for an implementation before acquiring resources.

Lyra requests all added and removed IDs in one lookup, passing each ID once. Each added ID must be absent from the base dataset, and each removal ID must exist there. Lyra checks even catalogs the metric never reads. Later runs can reuse a successful check for the same provider scope and intervention identity.

If you use [Parquet or PostGIS providers](#use-managed-parquet-and-postgis-providers), you inherit this method. These lookups read base IDs without creating temporary tables or requiring temporary-table privileges.

## Register and configure the resource

Register each provider class in the ordinary resource mapping:

```python
plugin = PluginDefinition(
    resources={"places": Places},
)
```

Import `PluginDefinition` from `lyra.sdk`; `Places` is your provider class. A provider-only plugin may omit metrics and operations. Every provider ID must identify exactly one registration across enabled plugins. The resource key selects configuration and lifecycle; requests and node declarations use the provider’s public `id`.

Configure the named resource’s settings and secret references through [resource configuration](../../operate/configuration/#configure-plugin-resources). Plain `LocationProvider` uses the empty `IOConfig`.

Custom configuration requires two declarations:

- `LocationProvider[CustomConfig]` sets the type of `self.config`
- `config_model = CustomConfig` selects runtime validation

Subclasses inherit both declarations unless you override them. The generic argument alone doesn’t select runtime validation. For local execution, pass settings through `resource_config` and fake subclasses through `resource_overrides`.

A metric declares a provider input with `inputs={"places": LocationDependency(provider=Places.id)}` and receives a native `GeoDataFrame`. Public requests supply `{"provider": Places.id, "ids": ["place-a"]}` for that input. An empty ID list is valid; each supplied ID must be a nonempty string.

## Manage acquisition and cleanup

Create clients in `setup()` and release them in `close()`, including clients created before setup fails. Declare `secret_names` and read resolved credentials from `self.secrets`. Wrap synchronous acquisition in `with self.operation("resolve"):` for resource logging.

Declare [resource dependencies](../resources/#compose-resources) as annotated attributes, such as `database: Annotated[DatabaseIO, Resource("city_db")]`. Register each dependency in `PluginDefinition.resources`. Subclasses inherit these attributes and may override their bindings.

Lyra initializes dependencies before the provider and shares one instance per registration within an execution scope. During cleanup, Lyra closes the provider before its dependencies. Don’t close injected resources in the provider’s `close()` method.

Keep acquisition in managed resources and provider resolution. Location operations map source IDs to existing IDs under a fixed target provider. Their handlers don’t acquire data or consume table dependencies. Provider preparation must not invoke a graph. Metrics may acquire supporting tables through ordinary metric dependencies.

Use the location records Lyra passes to your metric to calculate results for an intervention. If your metric reads the source file or database directly, it sees the original data.

## Define revision coverage

A provider scope identifies a dataset and its revision through these values:

- Public provider ID
- Provider code revision
- Origin identities

Together with `location_id`, the scope identifies a record’s geometry, attributes, and relationships. It also fixes the dataset’s complete membership. Equal IDs from different providers do not imply correspondence, and changing `DataFrame.attrs` cannot change scope.

Update an origin identity when its external contents change. Update the provider code revision or ID when changes affect its returned records or dataset membership. This includes changes to retrieval logic, transformations, or schemas.

Resource settings and acquisition batches do not independently change record identity. If configuration affects output, represent that difference in the provider or origin identity.

An omitted origin revision or provider code revision has the stable value `None`. Keep contents unchanged while their identities remain unchanged; `None` does not request the latest data. Lyra validates structure without detecting undeclared reads or auditing content changes.

Metrics declare `origins=(...)` for data they acquire directly. Sources consumed only through provider inputs or upstream outputs need no repeated direct declaration. Operations use their source and target provider scopes. These declarations participate in computation identity before cache lookup.

Lyra captures the declared scope before acquisition or remote acceptance and rejects declarations that conflict with that scope. It retains written provider records indefinitely. Your provider must honor the captured scope even when no retained record exists; see [cache identity and reuse](../../concepts/graphs/#reuse-tables-across-executions).

The [capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/capabilities.py) declares block and street providers with explicit implementation and origin revisions.

## Use managed Parquet and PostGIS providers

Managed providers resolve exact IDs and execute Ibis filters against the same registered collection. Import `ParquetLocationProvider` and `PostGISLocationProvider` from `lyra.sdk`; neither requires a custom resolver. Registration exposes their query capability and schema without accessing external sources.

The [managed provider declarations](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/managed.py) include complete schemas for PostGIS blocks and Parquet trees. Use those declarations alongside the configuration rules below and the [target selection reference](../../reference/target-selection/).

### Parquet file configuration

Configure `ParquetLocationProvider` with a string `path` naming one local file. For a provider registered as `tree_source`, supply:

```python {4}
from lyra.sdk import ResourceConfig

resource_config = {
    "tree_source": ResourceConfig(config={"path": "/data/trees.parquet"}),
}
```

Relative paths resolve from the executing process’s working directory. Lyra checks file availability when acquisition starts. The provider owns its embedded DuckDB backend and spatial support, including cleanup after failures.

The path cannot identify:

- A directory
- A glob pattern
- A collection of files
- A remote URL

Geometry decoding depends on the file format:

| Format | CRS source |
| --- | --- |
| GeoParquet | File geometry metadata, which must agree with the registered schema |
| Plain Parquet with WKB geometry | Registered schema |

Acquisition fails for conflicting CRS metadata, incompatible encodings, or invalid record types. Empty and all-null columns retain their registered types. Prepare column names and computed attributes upstream, and reproject geometry before writing the file.

### PostGIS relation configuration

A `PostGISLocationProvider` reads one existing PostgreSQL table, view, or materialized view. Declare the following fields alongside its identity, origins, and record schema:

| Declaration | Purpose |
| --- | --- |
| `db_schema = "public"` | PostgreSQL namespace |
| `table = "blocks"` | Existing relation name |
| `database: Annotated[PostgresIO, Resource("city_db")]` | Dependency on the registered database resource |

Import `Annotated` from `typing`; import `PostgresIO` and `Resource` from `lyra.sdk`. Configure connections and credentials on the [shared database resource](../resources/#share-a-native-postgresql-backend).

Expose every registered column through the relation. Use unique strings for location IDs and the declared CRS for geometry. For custom queries or transformations, define a view and use its name as `table`.

Read access to the relation covers exact-ID lookups and base membership checks. It also covers selectors when the request leaves this catalog unchanged.

If a selector queries a catalog with additions or removals, also grant the provider’s role `TEMPORARY` on the database. On the first such query, the provider loads added records and removal IDs into temporary tables. It reuses those tables for that catalog during the run and drops them before the shared connection closes.

### Collection identity

Configuration locates the collection fixed by the [provider scope](#define-revision-coverage). That scope combines provider ID, code revision, and origin identities. Update origin identities when external contents change; update the provider revision or ID when acquisition changes affect output.

Credentials and resource aliases do not define provider scope. Neither do query expressions or candidate IDs. Your acquisition code must honor the declared provider and origin identities.
