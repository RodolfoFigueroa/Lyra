---
title: How dependency graphs execute
description: Understand ordering, parameter forwarding, table reuse, and failures within a metric execution.
contentType: Conceptual
contentPlan: concepts/graphs
sidebar: { label: "How Dependency Graphs Execute" }
---

Metrics produce tables; location operations select or construct resolved locations. Table dependencies and [location bindings](../../plugins/dependencies/#4-forward-the-required-spatial-inputs) form one directed graph. Lyra validates the graph before execution, then runs the required nodes in dependency order so each consumer receives its inputs.

Handlers process whole location sets. Your plugin defines relationships, joins, spatial algorithms, and which features appear in the output. For authoring examples, see [Connect metrics and dependencies](../../plugins/dependencies/) and [Derive locations inside a graph](../../plugins/location-operations/).

## Start at the requested metric

The public request names one metric family and, when required, an edition. Before acquiring data, Lyra validates the request and fixes its routes and data revisions. A [canonical route](../../plugins/location-operations/#offer-canonical-public-selection) converts a source selection into the metric’s native location contract.

Execution resolves each route into native inputs, then follows the metric’s explicit calculation dependencies. Routing does not replace methodology or alter declared output granularity.

Each node has its own inputs. Dependencies do not add hidden public request fields. The runtime supplies contexts and dependency tables separately from the public request.

A dependency refers to a named output. Several inputs can consume the same producer, and a producer can publish several tables.

## Validate the complete graph

Registration and manifest generation check local declarations and signatures. They can run without installing referenced implementations or accessing sources.

When loading enabled manifests, Lyra checks the combined graph for:

- Valid output and operation references
- Name collisions
- Cycles across table dependencies and location bindings
- Valid parameter paths and required concrete location bindings
- Shared family structures and ordinary parameter contracts
- Satisfiable canonical operations, shortest-route preferences, and unambiguous accepted source contracts

The canonical conversion graph may contain cycles; each accepted execution plan must be acyclic. During execution, Lyra checks actual arguments, native return shapes, and public serialization.

These stages answer different questions. A valid local declaration does not prove that all dependency packages or source services exist in a deployment.

## Run sequentially on one worker

For server jobs, the requested metric’s queue selects the worker that executes the whole dependency graph. Dependencies execute sequentially on that worker, even when their metrics use different queues for direct requests.

That worker needs every plugin and source client required by the graph.

Local execution follows the same dependency structure in the caller’s process.

## Reuse tables within one execution

Identical metric invocations reuse their native tables within a job. Identical location operation invocations reuse their validated `Locations` values. This reuse includes every output from a producer that returns several tables.

Treat dependency tables as read-only because Lyra passes them by reference, without enforcing immutability. If your calculation needs to change a received table, make a copy first so other consumers retain the original values.

The requested metric returns all its declared outputs. Intermediate tables remain internal.

## Reuse tables across executions

Persistent caching lets later jobs reuse validated tables and location results from earlier jobs. For each invocation, Lyra prepares effective parameters and resolves location bindings before checking the cache. This preparation can execute operations and their table dependencies. After preparation, a cache hit skips the handler and remaining table dependencies.

Lyra identifies a cached invocation by:

- Node kind (metric or location operation) and name
- Normalized parameters, including defaults and validated values
- Resolved features, including IDs, geometries, properties, and coordinate reference system (CRS)
- Each input’s concrete source and geographic contract, including its edition, under the producer’s argument name
- Revisions of native location data and declared dependencies, including their own dependencies and an operation’s target dataset
- The computation namespace and optional run-wide `cache_revision`

Suppose metric `B` depends on metric `A`. A job calculates `A` with `year=2025`. A later job calculating `B` reuses those tables if its dependency requests `A` with the same effective inputs. Parameters used only by `B` do not affect reuse of `A`.

List order and receiving argument names affect cache identity. Caller names and execution paths do not. Lyra caches complete ordered batches; it does not guarantee reuse for individual IDs, subsets, or reordered inputs.

Canonical selectors execute or reuse cached selections before Lyra checks the metric cache. Different public selections can reuse a metric result when all cache inputs listed above match. Equal IDs alone are insufficient.

Revisions used only for public selection affect selector caches, but not the metric cache after native inputs match. Native dataset revisions and explicit calculation dependencies still invalidate the metric. A native revision change invalidates it even when returned values match.

A cache hit can still require source acquisition and intermediate artifact reads. Lyra records the current run’s original selection and accepted route in its [provenance](../../use/results/#inspect-execution-provenance). Execution observations distinguish cached results from newly executed nodes.

Each cache entry contains every output from one invocation, which Lyra publishes together. Lyra stores operation locations and named metric tables as uncompressed Arrow interprocess communication (IPC) files in versioned bundles. Constructing or caching locations does not register a provider collection.

When reading a cache entry, Lyra verifies stored types and content fingerprints. It then validates restored results against the current registered schemas and applies declared coercion. Empty results retain their declared columns and data types. Invalid cached results trigger recomputation; invalid handler results fail execution.

On a cache miss, Lyra finishes dependencies before acquiring the consumer’s cache lock. It then rechecks the cache, runs the handler, validates the results, and publishes them. Invocation locks do not nest. These filesystem locks prevent duplicate calculations among processes sharing a functioning cache on one host.

Processes sharing a cache directory must use equivalent implementations and resource settings. Lyra separates cached computations using a namespace derived from these declarations:

- Protocol versions
- Graph and schema contracts
- Available plugin versions
 Location revisions are separate: with an otherwise unchanged namespace, a revision-only change preserves unrelated cached results. There is no automatic source-code hashing.

Declare maintained dataset revisions on location contracts and document their [coverage and pinning limits](../../plugins/location-providers/#define-revision-coverage). An omitted revision means unversioned: Lyra can reuse matching contents without guaranteeing freshness.

Change the optional run-wide `cache_revision` for data changes outside declared coverage or for unversioned implementation changes. Lyra includes it alongside location revisions and computation identity for every node. A fresh value preserves earlier entries, and subsequent runs with that value can reuse results. You manage invalidation and cleanup.

Enable caching in [server configuration](../../operate/configuration/#cache-metric-tables) or [local execution](../../use/local-execution/#reuse-results-across-runs). If cache storage fails, Lyra logs a warning and continues execution; later requests may repeat the calculation.

## Stop at the first failure

A failed invocation stops the graph. The job returns no partial result and does not retry automatically. Diagnostics identify the failing node and dependency path.

Lyra never caches failed invocations. Successful dependencies can remain cached when a later calculation fails, so a new submission can reuse them.

Use [plugin tests](../../plugins/testing/) to verify forwarding and reuse with fake sources. See [troubleshooting](../../operate/troubleshooting/#diagnose-a-graph-failure) to investigate deployed failures.
