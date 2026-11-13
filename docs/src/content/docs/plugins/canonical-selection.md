---
title: Accept additional location types
description: Offer canonical selectors and resolve shortest-route ties across enabled plugins.
contentType: How-to
contentPlan: plugins/canonical-selection
sidebar: { label: "Accept Additional Location Types" }
---

Offer a location operation for automatic public selection when callers should be able to select a source type different from the metric’s native input. [The city tutorial](../tutorial/canonical/) demonstrates a complete request and its street-level result.

## Offer canonical public selection

Set `canonical=True` on a [location operation](../location-operations/) to let Lyra select it automatically. Register the decorated function in `PluginDefinition(location_operations=[...])`.

The operation must meet these requirements:

- Declare exactly one provider source through `inputs` and a target provider ID through `target`.
- Use defaults or fixed bindings for optional typed parameters, without extra caller arguments.
- Compute from the source frame without resource acquisition, direct origins, or table dependencies.
- Use optional `RunContext` only for reporting.

Return each related target record once, in deterministic order. Every row must match the target provider’s schema and [record scope](../location-providers/#define-revision-coverage). Include every record the operation needs in the source provider’s attributes.

Downstream handlers must support empty intermediate selections because they still execute when selection finds no targets. Preserve membership relationships explicitly when the calculation needs them. Set selection does not retain source-to-target associations or aggregate results.

Canonical selection supplies the public metric’s native inputs while explicit dependencies preserve its methodology. Selecting blocks for a street rating still returns street rows. The [city block aggregation](../tutorial/blocks/) produces block rows because its calculation explicitly aggregates street scores.

Each `Input(...)` reference reads the effective native inputs of the node that declares it. Supply compatible bindings for internal dependencies; automatic routing does not repair mismatches. Canonical selectors use their own defaults and fixed bindings without inheriting metric parameters by name.

Canonical operations are optional. A plugin can use only explicit noncanonical operations; compatible routes may come from other enabled plugins. Without a route to an input’s native contract, callers must select that native type. Accepted selections identify source providers by their public IDs.

Lyra compiles shortest routes across the complete enabled catalog, counting every operation equally. An exact provider scope match uses zero operations; matching schemas or bare IDs is insufficient.

Routes in opposite directions are independent, and the conversion graph can contain cycles. Each request’s selected routes and calculation dependencies must form an execution plan without cycles.

Discovery lists supported sources for each input. Lyra validates your chosen combination during request preparation. It rejects execution cycles before queueing the job or acquiring data.

Canonical means the chosen default, not equivalence between routes. A shortcut can change both results and performance. Execution keeps the accepted path, including after failures or when another path has cached results.

See [the capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/capabilities.py) for batched selectors, private membership, and a route preference. Its [usage guide](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) also demonstrates native-only inputs and empty intermediates.

## Choose a route when shortest paths tie

When several canonical routes connect the same contracts with the fewest operations, declare a preference for that pair. Canonical operations declare `canonical=True` to participate in route selection. Each tied source and target pair requires its own preference, shared across all metrics.

Pass an ordered sequence of decorated operation references to `PluginDefinition(route_preferences=[(first_operation, second_operation)])`. Discovery describes their concrete names. During catalog compilation, Lyra infers the source and target provider scopes from the sequence.

Lyra rejects preferences that:

- Reference unknown or noncanonical operations
- Contain no operations or connect operations with mismatched contracts
- Use more operations than a shortest route
- Conflict with another preference for the same source and target

Adding a shortcut invalidates a preference if the preferred route becomes longer than the shortest route. Removing an alternative leaves the preference valid if its route remains shortest, even when no tie remains.
