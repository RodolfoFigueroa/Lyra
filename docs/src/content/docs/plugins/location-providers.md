---
title: Define a location provider
description: Declare collections and implement exact batch resolution through a named resource.
contentType: Reference
contentPlan: plugins/location-providers
sidebar: { label: "Define A Location Provider" }
---

A location provider resolves string IDs within a collection and unit type into feature collections. Implement `LocationProvider[Config]` and register it as a named resource. The [blocks and lots tutorial](../location-example/) demonstrates the complete contract without services.

## Declare collections

Set `config_model` to your resource configuration model and `collections` to a nonempty tuple of `LocationCollection` values. Each declaration contains these fields:

| Field | Contract |
| --- | --- |
| `name` | Unique namespaced name, such as `transport:stations` |
| `unit_types` | Nonempty tuple of unique provider-defined categories |
| `crs` | Coordinate reference system (CRS) identifier for every returned geometry |

Unit types need no hierarchy. IDs are opaque strings and need no census format. Providers can return Point, MultiPoint, LineString, MultiLineString, Polygon, and MultiPolygon geometries. Within a collection and unit type, an ID must always identify the same geometry.

Declare metadata without creating clients or reading secrets. Manifest generation and plugin inspection expose collections without acquiring resources. Input descriptions explain calculation requirements; Lyra does not enforce a collection-to-metric compatibility framework.

## Resolve a batch

Implement synchronous `resolve(collection: str, unit_type: str, ids: list[str]) -> LocationInput`. Lyra validates the collection and unit type, then removes duplicate IDs while preserving their first-occurrence order. The resulting list is nonempty. Return each requested ID exactly once. Include its geometry and properties dictionary, and set the feature collection’s declared CRS.

Lyra checks for missing, duplicate, or extra features and incorrect CRS. Preserve ID spelling and resolve only the requested units. Properties may be empty; document attributes that calculations depend on.

Use these errors to distinguish invalid selections from acquisition failures:

| Error | Provider action |
| --- | --- |
| `UnresolvedLocationIDsError(collection, unit_type, ids)` | Include every unknown or wrong-type ID, including when none resolve |
| `LocationAcquisitionError(message, collection=…, unit_type=…, ids=…)` | Chain the underlying service failure; exclude credentials from the message |

The resolver adds the offending input name. A malformed selection raises `LocationSelectionError`; invalid provider output raises `LocationProviderContractError`. Server submission rejects invalid selections before creating a job and reports acquisition failures as `location_provider_unavailable`.

## Register and configure the resource

Register the class and designate its resource name:

```python
plugin = PluginDefinition(
    resources={"places": Places},
    location_provider="places",
)
```

Import `PluginDefinition` from `lyra.sdk`; `Places` is your provider class. A provider-only plugin omits `nodes`. A mixed plugin also supplies its metric nodes. Exactly one enabled plugin may designate a provider, even when multiple declarations refer to the same resource name.

Configure settings and secret references under the named resource through [resource configuration](../../operate/configuration/#configure-plugin-resources). Local runners accept `resource_config` and fake subclasses through `resource_overrides`. Requests select collections and unit types through the deployment’s provider.

## Manage acquisition and cleanup

Create clients in `setup()` and release them in `close()`, including clients created before a setup failure. Declare `secret_names` and read resolved credentials from `self.secrets`. Wrap synchronous acquisition in `with self.operation("resolve"):` for resource logging.

Lyra initializes one provider instance in a shared scope for all named selections in a submission. It closes that scope before enqueueing or local graph execution, including on resolution failure. Server acquisition runs outside the event loop; submission waits for completion. Workers consume the resolved selections without another provider lookup.

Graph execution uses a separate resource scope for calculation data. Metrics may acquire supporting tables through producers that have no location arguments. See [Connect metrics and dependencies](../dependencies/) for a location-free producer and ordinary parameter defaults.
