---
title: Define a location provider
description: Declare collections and implement exact batch resolution through a named resource.
contentType: Reference
contentPlan: plugins/location-providers
sidebar: { label: "Define A Location Provider" }
---

A location provider resolves string IDs within a collection and unit type into shared `Locations` values. Implement `LocationProvider[Config]` and register it as a named resource. The [blocks and lots tutorial](../location-example/) demonstrates the complete contract without services.

## Declare collections

Set `config_model` to your resource configuration model and `collections` to a nonempty tuple of `LocationCollection` values. Each declaration contains these fields:

| Field | Contract |
| --- | --- |
| `name` | Unique namespaced name, such as `transport:stations` |
| `unit_types` | Nonempty tuple of unique provider-defined categories |
| `crs` | Coordinate reference system (CRS) identifier for every returned geometry |

Unit types need no hierarchy. IDs are opaque strings and need no census format. Providers can return Point, MultiPoint, LineString, MultiLineString, Polygon, and MultiPolygon geometries. IDs identify features within a collection and unit type; their contents may change.

Declare metadata without creating clients or reading secrets. Manifest generation and plugin inspection expose collections without acquiring resources. Input descriptions explain calculation requirements; Lyra does not enforce a collection-to-metric compatibility framework.

## Declare output schemas

Map every `(collection, unit_type)` pair to a `LocationOutputSchema` in the provider class’s `outputs` attribute. Construct each contract from a Pandera `DataFrameSchema` or `DataFrameModel`. Declare the ID, geometry, and property columns with descriptions and unit metadata. Set `id_column` and `geometry_column` when their names differ from the defaults, `id` and `geometry`.

Schema compilation requires no services. Inspection exposes each collection’s `outputs` metadata keyed by unit type. Lyra validates output from resource overrides against the registered provider’s schemas.

The resolver validates properties and applies declared type coercion before passing values to handlers or computing invocation identities. Missing and unexpected properties fail validation. Empty frames are valid internally when the schema and consumer permit them; public selections require IDs.

## Resolve a batch

Implement a synchronous `resolve` method accepting `collection: str`, `unit_type: str`, and `ids: list[str]`. Return a `GeoDataFrame`. Lyra validates the collection and unit type, then removes duplicate IDs while preserving their first-occurrence order. The resulting list is nonempty.

Return a `GeoDataFrame` with each requested ID exactly once, its geometry, and its properties. Set the collection’s declared CRS and include every schema column, even in empty frames.

Lyra validates the frame against the registered schema and restores request order. It then constructs an immutable `Locations` value with the requested collection and unit type.

Direct calls to `resolve` return frames without validation. To validate one, call `schema.build(frame, collection=collection, unit_type=unit_type)` with the registered schema and requested scope.

Lyra rejects missing, duplicate, or extra features and incorrect CRS. Preserve ID spelling and resolve only the requested units. Properties may be empty when the schema permits; document attributes that calculations depend on.

Use these errors to distinguish invalid selections from acquisition failures:

| Error | Provider action |
| --- | --- |
| `UnresolvedLocationIDsError(collection, unit_type, ids)` | Include every unknown or wrong-type ID, including when none resolve |
| `LocationAcquisitionError(message, collection=…, unit_type=…, ids=…)` | Chain the underlying service failure; exclude credentials from the message |

The resolver adds the offending input name. A malformed selection raises `LocationSelectionError`; invalid provider output raises `LocationProviderContractError`. Server submission rejects structurally malformed selections before creating a job. Resolution and acquisition errors fail execution with node and input context.

## Register and configure the resource

Register the class and designate its resource name:

```python
plugin = PluginDefinition(
    resources={"places": Places},
    location_provider="places",
)
```

Import `PluginDefinition` from `lyra.sdk`; `Places` is your provider class. A provider-only plugin omits `metrics`. A mixed plugin also supplies its metric nodes. Exactly one enabled plugin may designate a provider, even when multiple declarations refer to the same resource name.

Configure settings and secret references under the named resource through [resource configuration](../../operate/configuration/#configure-plugin-resources). Local runners accept `resource_config` and fake subclasses through `resource_overrides`. Requests select collections and unit types through the deployment’s provider.

## Manage acquisition and cleanup

Create clients in `setup()` and release them in `close()`, including clients created before a setup failure. Declare `secret_names` and read resolved credentials from `self.secrets`. Wrap synchronous acquisition in `with self.operation("resolve"):` for resource logging.

Local runners and workers share one provider instance between location resolution and handlers. They close its resources when execution ends, including on failure. The API queues validated ID selections without constructing or acquiring the provider.

Prepare provider collections from configured sources during the managed resource lifecycle. Your plugin can cache this preparation, but must not invoke a graph during it. If construction needs graph outputs, use an [operation with explicit table-to-location conversion](../location-operations/#convert-tables-and-construct-entities).

Empty internal lookups validate scope and schema without acquiring the provider. The resolver removes duplicate IDs and returns features in the order each ID first appears in the request. Empty results still require valid scope, CRS, and schema.

Metrics may acquire supporting tables through producers that have no location arguments. See [Connect metrics and dependencies](../dependencies/) for a location-free producer and ordinary parameter defaults.
