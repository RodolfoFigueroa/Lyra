# Capability contracts for plugin authors

Read this reference when choosing source identity, registering editions, offering
canonical selection, or defining revision coverage. It complements the
[adapter and resource contract](authoring.md).

## Geographic units and source observations

A geographic unit is identified by collection, unit type, geographical edition,
and string ID. Equal IDs in different geographical editions imply no
correspondence. Two observations of the same geographic unit can have different
attributes and definitions; they are not interchangeable input data.

Build a shared `LocationFamily` with
`LocationFamily.from_schema(collection, unit_type, schema)`. Its structure covers
columns, types, nullability, constraints, ID/geometry conventions, and units.
Reuse that family in concrete `LocationContract` declarations:

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

A concrete reference includes collection, unit type, source, geographical
edition, and optional source edition. Choose identities that distinguish source
observations, including distinct periods or releases. Descriptive metadata alone
does not create a new source identity. Several periods may use one geography;
source edition, release, period, and geographical edition remain distinct.

Provider-backed inputs and outputs reuse the provider's contracts. Map every
named `Locations` argument through `locations={name: contract}`; operations also
supply `output_contract`. Handlers receive ordinary resolved location values.
Preserve column definitions and source meaning even when schemas match: shared
structure does not establish statistical comparability.

## Families, editions, and private intermediates

Use ordinary Python factories to create concrete graphs. Each decorated node has
a distinct `name`; associate metric variants with `family`, select their named
`edition`, and explain source releases, periods, and fixed methodological
constants through the JSON `edition_metadata` object. Register every generated
handler in `PluginDefinition`. Corresponding nodes share family metadata, not
implicit dependency edges.

A metric family shares named native input roles and location families, named
output schemas, and the complete ordinary parameter contract: names, types,
requiredness, nullability, defaults, bounds, allowed values, and other declared
constraints. Concrete source bindings, dependency bindings, values, boundaries,
available IDs, and definitions may differ. Incompatible public parameter
contracts require separate families or a common contract supported by every
variant.

Bind tables with `Dependency(output_name, producer=concrete_node_name)` and
locations with `LocationDependency(concrete_operation_name, ...)`. Equal local
output names across variants do not connect their graphs. Share a dependency
explicitly by naming the same concrete producer/output pair.

An omitted `edition` gives one implicit variant with no public selector or
mandatory factory. Explicitly named editions require selection even when only
one exists; there is no implicit latest. Edition selection chooses concrete
source and geographic bindings, independently of ordinary handler parameters.

Metrics are public by default. Set `private=True` on intermediate metrics to
exclude them from public discovery and direct submission while retaining normal
dependency execution and caching. Privacy controls entry points, not secrecy;
relevant graph inspection can show private nodes. Use direct function calls or
`runner.run_development(concrete_metric, inputs)` with resolved locations for
development access. Operations use canonical status to control routing.

Declare output `meaning` and `granularity` in Pandera schema metadata and retain
column descriptions and units. A route that selects tracts from a state still
feeds a tract calculation and returns tract rows. Aggregation requires an
explicit calculation and declared output meaning.

## Public selection and calculation methodology

Public local, HTTP, Python-client, and MCP requests select a public metric
family, an edition when required, ordinary parameters, named source ID
selections, and optional `cache_revision`. Each selection contains only
`collection`, `unit_type`, and nonempty string `ids`. The selected variant binds
one concrete source contract for each input and accepted collection/unit-type
pair; catalog compilation rejects ambiguity.

For several reachable contracts sharing a source scope, declare
`source_bindings={input_name: (source_contract.reference,)}` on the metric.
Bindings must be reachable, unique per source scope, and preserve the native
contract for direct native selection. Unambiguous sources need no override.

Opt an operation into automatic routing with `canonical=True`. It must have
exactly one named location-set input and one location-set output. Resources,
context, fixed/default parameters, and internal dependencies are allowed when
satisfiable from that input and declared bindings without extra caller arguments.
Canonical selectors use their own defaults/fixed bindings, not matching metric
parameters.

A canonical selector accepts a batch and returns the union of related targets,
each identity once in deterministic order, as complete rows under its output
contract. It can obtain all rows in one query without separate acquisition.
Empty intermediates continue to handlers, which must support them. Retain
source-to-target membership explicitly when methodology needs it; set selection
does not imply grouping or aggregation.

Canonical operations are optional. Explicit dependencies can use noncanonical
operations, and other enabled plugins may supply compatible canonical routes.
Without a route, a public input accepts its native type only. Requests do not
name operations or construct pipelines.

The complete catalog chooses the fewest operations, with equal cost per edge;
exact concrete contract matches use zero operations. For shortest-path ties,
register ordered decorated operation references through
`PluginDefinition(route_preferences=[(first_operation, second_operation)])`.
Each preference applies to its inferred concrete source/target pair across
metrics. It must remain canonical, connected, shortest, and nonconflicting.
Invalid preferences fail compilation; one naming the unique shortest path is
still valid.

Opposite-direction edges do not imply inverses. Conversion-graph cycles are
allowed, while accepted execution plans must be acyclic. Same-contract inputs
never expand through a round trip. Alternative paths need not yield equivalent
locations; adding a shortcut may change results. Execution keeps the accepted
route instead of rerouting after failure or toward warmer caches.

Canonical selection resolves native inputs before ordinary dependency
forwarding. Every `Input(...)` sees the effective inputs of its declaration
owner. Explicit bindings override matching-name forwarding, then producer
parameter defaults fill remaining fields. Internal type mismatches require
explicit compatible bindings; public routing does not replace methodology.

## Revisions, cache reuse, and pinning

Set `data_revision` on location definitions and document its coverage. It covers
the dataset's geometry, attributes, and included relationships, even across
files or tables. Unrelated reads by a handler are not implicitly covered. A
correction can advance the revision without changing geographic identity or
introducing a selectable edition. Equal revision strings on different contracts
do not identify the same dataset.

Metrics and operations inherit revisions from native inputs and transitive
explicit dependencies. Location-producing operations also include the target
contract's revision. Authors do not repeat transitive revisions or declare
independent node revisions. Purely derived locations can rely on declared
dependencies without an independent revision.

Omitted revisions mean unversioned, including for implicit editions. Normal
content-based reuse continues, provenance records the absence, and Lyra promises
no automatic freshness. Use `cache_revision` for external changes outside
location-revision coverage, unversioned implementation changes, or deliberate
recomputation. It adds an identity component to every node in that run alongside
location revisions and computation identity. Repeating a value permits reuse;
a fresh value preserves earlier entries rather than purging a server cache.

Preparation pins declared revisions before local acquisition or remote
acceptance. Providers must serve the requested revision or fail clearly; never
substitute newer data. Pinning metadata cannot detect unreported changes or
create storage snapshots. Historical retention is not required: failing an
outdated queued job is valid.

Canonical selectors execute or reuse cached values before the metric lookup.
Metric reuse depends on complete ordered native contents, concrete contracts,
effective parameters, calculation revisions, and computation identity. Revisions
used only for public selection stop at the native-input boundary; native dataset
and explicit methodology revisions still apply. A native revision change
invalidates the metric even when values match. Equal IDs alone do not permit
reuse, and there is no per-ID, subset, permutation-insensitive, or zero-I/O cache
guarantee. The structural namespace includes graph contracts and available
package versions, not automatic source-code hashes. Revision-only changes leave
unrelated cache entries reusable when that namespace is unchanged.

## Run and inspect the contract

`LocalRunner.run(family, inputs, edition=..., cache_revision=...)` returns named
native DataFrames/GeoDataFrames. Omit `edition` for an implicit variant. Inspect
provenance separately with `runner.inspect()`: original selections, effective
parameters, concrete identity, routes, pinned revisions, and observed native
counts/fingerprints. Accepted plans and execution observations are separate;
a planned dependency must not be described as executed without an observation.
Cached native results carry the current run's provenance.

Remote consumers list/search families, inspect an explicit edition when needed,
submit once, poll the returned job handle, preview bounded results, and retrieve
full results with authentication. Family inspection gives shared schemas and
public edition choices; concrete inspection adds exact source bindings and
accepted selections. Search preserves edition-specific source availability.

Malformed or unsupported requests fail before acquisition or job creation.
Unknown IDs fail execution with input, source scope, and unresolved IDs; an
existing source with no related targets continues as an empty selection.
Inspecting a failed remote job is a successful status lookup, not a failed
inspection request.

Reserve MCP `lyra_get_metric_dependencies` for calculation-relationship questions.
It returns direct dependencies and consumers for a concrete public variant, with
private markers, producer outputs, receiving input names, and explicit omitted
counts at a limit of 50 relationships. Declared operation dependencies are
included; possible canonical routes are not extra calculation dependencies.
Routine execution and successful result interpretation do not require it.

The service-free [capability example and usage guide](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md)
connect these contracts: `capabilities.py` has edition factories, private
producers, batched route ties, native-only inputs, empty selections, two named
inputs, and revision coverage. `remote.py` demonstrates discovery, edition
inspection, submission, polling, and retrieval; `tests/test_capability_examples.py`
exercises it through an in-process fake transport. Use the source version matching
your SDK. These examples supplement the bundled contract; adapting a calculation
does not require a Lyra checkout or a live service.
