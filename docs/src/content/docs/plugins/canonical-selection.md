---
title: Accept additional location types
description: Offer canonical selectors and resolve shortest-route ties across enabled plugins.
contentType: How-to
contentPlan: plugins/canonical-selection
sidebar: { label: "Accept Additional Location Types" }
---

Register a canonical operation to convert selections automatically when their provider differs from an input’s required provider. Lyra applies these routes to public selections and dependency inputs. [The city tutorial](../tutorial/canonical/) demonstrates a public block selection that produces street results.

## Offer canonical public selection

Set `canonical=True` on a [location operation](../location-operations/) to let Lyra select it automatically. Register the decorated function in `PluginDefinition(location_operations=[...])`.

The operation must meet these requirements:

- Declare exactly one provider source through `inputs` and a target provider ID through `target_provider`.
- Use defaults or fixed bindings for optional typed parameters, without extra caller arguments.
- Compute from the source frame without resource acquisition, direct origins, or table dependencies.
- Accept the source GeoDataFrame positionally. If you need parameters, add keyword-only `parameters` annotated with a `MetricParameters` subclass.

Return a dictionary mapping each source ID to a tuple of target IDs. Lyra resolves those IDs through the fixed target provider. Each forwarded row contains `source_location_id` alongside the target record’s `location_id`.

When sources share a target, Lyra forwards one row per source/target pair, called a correspondence. Empty or omitted groups complete with no matches. Missing target records fail acquisition.

Make downstream metrics handle empty tables with their declared schemas. Deduplicate `location_id` when you calculate once per target record. Keep every pair when you calculate relationships, and declare [stable row keys](../outputs/#declare-location-ownership).

Routing supplies records from the provider each input declares. Selecting blocks for a street rating still returns street rows. The [city block aggregation](../tutorial/blocks/) produces block rows because its calculation aggregates street scores.

Use [dependency bindings](../dependencies/#4-forward-the-required-spatial-inputs) to select which caller locations a dependency receives. Each `Input(...)` reference reads the declaring caller’s effective inputs. Lyra then routes those locations to the provider required by the dependency’s input.

An explicitly bound operation keeps its fixed target provider. If the next input requires another provider, Lyra adds a canonical route. Automatic operations use their own defaults and fixed bindings; they don’t inherit matching metric parameters.

Your plugin can use explicit operations and rely on other enabled plugins to register canonical routes. Lyra considers all enabled plugins and chooses the path with the fewest operations. If you select the input’s target provider, Lyra uses its records directly. Other source providers need a canonical route to that target.

Routes in opposite directions are independent, and the conversion graph can contain cycles. Each request’s selected routes and calculation dependencies must form an execution plan without cycles.

Inspect discovery results to choose a supported source for each public input. Lyra validates the complete graph before queueing a job or acquiring data. Preparation rejects:

- Missing required routes
- Shortest-route ties without a preference
- Execution cycles

The same route preferences apply to public selections and dependency inputs.

Canonical means the chosen default, not equivalence between routes. A shortcut can change both results and performance. Execution keeps the accepted path, including after failures or when another path has cached results.

See [the capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/capabilities.py) for batched selectors, private membership, and a route preference. Its [usage guide](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) also demonstrates native-only inputs and empty intermediates.

## Choose a route when shortest paths tie

If two shortest routes connect the same source and target providers, declare which route Lyra should use. Set `route_preferences` on `PluginDefinition` to a list of operation sequences, such as `[(first_operation, second_operation)]`. List the decorated functions in execution order.

Lyra takes the source provider from the first operation and the target provider from the last. The preference applies to that provider pair across all metrics. Declare a preference for each pair with a tie.

Lyra rejects preferences that:

- Reference unknown or noncanonical operations
- Contain no operations
- Have a gap between consecutive operations (the first target must match the next source)
- Use more operations than a shortest route
- Conflict with another preference for the same source and target

Adding a shortcut invalidates a preference if the preferred route becomes longer than the shortest route. Removing an alternative leaves the preference valid if its route remains shortest, even when no tie remains.
