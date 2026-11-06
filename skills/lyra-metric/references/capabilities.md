# Capability contracts for plugin authors

Read this reference when choosing source identity, registering editions, offering canonical selection, or defining revision coverage. It complements the [adapter and resource contract](authoring.md).

## Geographic units and source observations

Identify a geographic unit by its collection, unit type, geographical edition, and string ID. Equal IDs in different geographical editions imply no correspondence. Two observations of the same geographic unit can have different attributes and definitions; they are not interchangeable input data.

Build a shared `LocationFamily` with `schema.family(collection, unit_type)`. Its structure covers columns, types, nullability, constraints, ID/geometry conventions, and units. Reuse that family in concrete `LocationContract` declarations:

| Field | Meaning |
| --- | --- |
| `family` | Shared collection, unit type, and structural schema |
| `source` | Identifier distinguishing concrete source observations |
| `geographical_edition` | Geographic boundaries and unit identities |
| `edition` | Optional named source-contract edition |
| `meaning` | Description of the source data and its interpretation |
| `observation_period` | Optional period described by the observations |
| `release` | Optional source release |
| `field_definitions` | Meanings keyed by declared field names |
| `data_revision` | Optional maintained data version for this concrete contract |

A concrete reference includes collection, unit type, source, geographical edition, and optional source edition. Choose identities that distinguish source observations, including distinct periods or releases. Descriptive metadata alone does not create a new source identity. Several periods may use one geography; source edition, release, period, and geographical edition remain distinct.

Provider-backed inputs and outputs reuse the provider’s contracts. Map every named `Locations` argument through `locations={name: contract}`; operations also supply `output_contract`. Handlers receive ordinary resolved location values. Preserve column definitions and source meaning even when schemas match: shared structure does not establish statistical comparability.

## Families, editions, and private intermediates

Use ordinary Python factories to create concrete graphs. Each decorated node has a distinct `name`; associate metric variants with `family`, select their named `edition`, and explain source releases, periods, and fixed methodological constants through the JSON `edition_metadata` object. Register every generated handler in `PluginDefinition`. Corresponding nodes share family metadata, not implicit dependency edges.

A metric family shares named native input roles and location families, named output schemas, and the complete ordinary parameter contract: names, types, requiredness, nullability, defaults, bounds, allowed values, and other declared constraints. Concrete source bindings, dependency bindings, values, boundaries, available IDs, and definitions may differ. Incompatible public parameter contracts require separate families or a common contract supported by every variant.

Bind tables with `Dependency(output_name, producer=concrete_node_name)` and locations with `LocationDependency(concrete_operation_name, ...)`. Equal local output names across variants do not connect their graphs. Share a dependency explicitly by naming the same concrete producer/output pair.

An omitted `edition` gives one implicit variant with no public selector or mandatory factory. Explicitly named editions require selection even when only one exists; there is no implicit latest. Edition selection chooses concrete source and geographic bindings, independently of ordinary handler parameters.

Metrics are public by default. Set `private=True` on intermediate metrics to exclude them from public discovery and direct submission while retaining normal dependency execution and caching. Privacy controls entry points, not secrecy; relevant graph inspection can show private nodes. Use direct function calls or `runner.run_development(concrete_metric, inputs)` with resolved locations for development access. Operations use canonical status to control routing.

Declare output `meaning` and `granularity` in Pandera schema metadata and retain column descriptions and units. A route that selects tracts from a state still feeds a tract calculation and returns tract rows. Aggregation requires an explicit calculation and declared output meaning.

## Public selection and calculation methodology

Public local, HTTP, Python-client, and MCP requests select a public metric family, an edition when required, ordinary parameters, named source ID selections, and optional `cache_revision`. Each selection contains `collection`, `unit_type`, and `ids`. Supply at least one ID, with each ID a nonempty string. The selected variant binds one concrete source contract for each input and accepted collection/unit-type pair; catalog compilation rejects ambiguity.

For several reachable contracts sharing a source scope, declare `source_bindings={input_name: (source_contract.reference,)}` on the metric. Bindings must be reachable, unique per source scope, and preserve the native contract for direct native selection. Unambiguous sources need no override.

Opt an operation into automatic routing with `canonical=True`. It must have exactly one named location-set input and one location-set output. Resources, context, fixed/default parameters, and internal dependencies are allowed when satisfiable from that input and declared bindings without extra caller arguments. Canonical selectors use their own defaults/fixed bindings, not matching metric parameters.

A canonical selector accepts a batch and returns the union of related targets, each identity once in deterministic order, as complete rows under its output contract. It can obtain all rows in one query without separate acquisition. Empty intermediates continue to handlers, which must support them. Retain source-to-target membership explicitly when methodology needs it; set selection does not imply grouping or aggregation.

Canonical operations are optional. Explicit dependencies can use noncanonical operations, and other enabled plugins may supply compatible canonical routes. Without a route, a public input accepts its native type only. Requests do not name operations or construct pipelines.

Lyra chooses the route with the fewest operations across the enabled catalog. An exact contract match requires zero operations. For shortest-path ties, register ordered decorated operation references through `PluginDefinition(route_preferences=[(first_operation, second_operation)])`. Each preference applies to its inferred concrete source/target pair across metrics. It must remain canonical, connected, shortest, and nonconflicting. Invalid preferences fail catalog compilation. A preference may name the only shortest path.

Opposite-direction edges do not imply inverses. Conversion-graph cycles are allowed, while accepted execution plans must be acyclic. Same-contract inputs never expand through a round trip. Alternative paths need not yield equivalent locations; adding a shortcut may change results. Execution keeps the accepted route instead of rerouting after failure or toward warmer caches.

Canonical selection resolves native inputs before ordinary dependency forwarding. Every `Input(...)` sees the effective inputs of its declaration owner. Explicit bindings override matching-name forwarding, then producer parameter defaults fill remaining fields. Internal type mismatches require explicit compatible bindings; public routing does not replace methodology.

## Revisions, cache reuse, and pinning

Set `data_revision` on location definitions and document its coverage. It covers the dataset’s geometry, attributes, and included relationships, even across files or tables. Unrelated reads by a handler are not implicitly covered. A correction can advance the revision without changing geographic identity or introducing a selectable edition. Equal revision strings on different contracts do not identify the same dataset.

Metrics and operations inherit revisions from native inputs and transitive explicit dependencies. Location-producing operations also include the target contract’s revision. Authors do not repeat transitive revisions or declare independent node revisions. Purely derived locations can rely on declared dependencies without an independent revision.

Omitted revisions mean unversioned, including for implicit editions. Lyra records the missing revision in provenance and can still reuse matching content. You must track changes to unversioned sources. Use `cache_revision` for external changes outside location-revision coverage, unversioned implementation changes, or deliberate recomputation. It adds an identity component to every node in that run alongside location revisions and computation identity. Repeating a value permits reuse; a fresh value preserves earlier entries rather than purging a server cache.

Preparation pins declared revisions before local acquisition or remote acceptance. Providers must serve the requested revision or fail clearly; never substitute newer data. Pinning metadata cannot detect unreported changes or create storage snapshots. Historical retention is not required: failing an outdated queued job is valid.

The runtime resolves canonical selections before it checks the metric cache. It can reuse a complete, ordered batch when native contents, concrete contracts, effective parameters, calculation revisions, and computation identity match. A lookup can still require acquisition or intermediate reads. Changing the order or membership of the resolved native batch requires a separate lookup.

A revision used only to select locations affects the selector’s cache. A change to a native dataset or explicit dependency revision invalidates the metric even when the values match. The runtime computes each node’s computation identity from its contracts, dependencies, protocol versions, and installed plugin versions. Changes to source code alone can leave that identity unchanged.

## Run and inspect the contract

`LocalRunner.run(family, inputs, edition=..., cache_revision=...)` returns a dictionary of native DataFrames or GeoDataFrames keyed by output name. Omit `edition` for an implicit variant. Call `runner.inspect()` separately to compare the accepted plan with what ran.

The plan records the original selections, effective parameters, concrete metric, routes, and pinned revisions. Execution observations record native input counts and fingerprints. Use those observations to distinguish a completed calculation from a cache hit. Even when Lyra reuses tables, provenance describes the current run.

On a server, search results show which source types each edition accepts. Inspect the family for shared schemas and edition choices, then inspect a named edition for its exact source bindings and accepted selections. Submit once, poll the returned job handle, then preview the results or download them with your agent credentials.

Malformed or unsupported requests fail before acquisition or job creation. Unknown IDs fail execution with input, source scope, and unresolved IDs; an existing source with no related targets continues as an empty selection. Inspection can succeed for a failed remote job. Read the returned job status to distinguish execution failure from a failed inspection request.

Reserve MCP `lyra_get_metric_dependencies` for calculation-relationship questions. It returns direct dependencies and consumers for a concrete public variant, with private markers, producer outputs, receiving input names, and explicit omitted counts at a limit of 50 relationships. Declared operation dependencies are included; possible canonical routes are not extra calculation dependencies. Routine execution and successful result interpretation do not require it.

The service-free [capability example and usage guide](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) connect these contracts: `capabilities.py` has edition factories, private producers, batched route ties, native-only inputs, empty selections, two named inputs, and revision coverage. `remote.py` demonstrates discovery, edition inspection, submission, polling, and retrieval; `tests/test_capability_examples.py` exercises it through an in-process fake transport. Use the source version matching your SDK. These examples supplement the bundled contract; adapting a calculation does not require a Lyra checkout or a live service.
