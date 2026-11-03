---
title: How persistent caching works
description: Understand invocation identity, revision coverage, publication, validation, and reuse across runs.
contentType: Conceptual
contentPlan: concepts/caching
sidebar: { label: "How Persistent Caching Works" }
---

Persistent caching reuses validated metric tables and operation locations across executions. It depends on concrete inputs, declared revisions, and equivalent implementations and resource settings.

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

## Store and validate cached results

Each cache entry contains every output from one invocation, which Lyra publishes together. Lyra stores operation locations and named metric tables as uncompressed Arrow interprocess communication (IPC) files in versioned bundles. Constructing or caching locations does not register a provider collection.

When reading a cache entry, Lyra verifies stored types and content fingerprints. It then validates restored results against the current registered schemas and applies declared coercion. Empty results retain their declared columns and data types. Invalid cached results trigger recomputation; invalid handler results fail execution.

## Coordinate concurrent calculations

On a cache miss, Lyra finishes dependencies before acquiring the consumer’s cache lock. It then rechecks the cache, runs the handler, validates the results, and publishes them. Invocation locks do not nest. These filesystem locks prevent duplicate calculations among processes sharing a functioning cache on one host.

## Keep cached results current

Processes sharing a cache directory must use equivalent implementations and resource settings. Lyra separates cached computations using a namespace derived from these declarations:

- Protocol versions
- Graph and schema contracts
- Available plugin versions

Location revisions are separate: with an otherwise unchanged namespace, a revision-only change preserves unrelated cached results. There is no automatic source-code hashing.

Declare maintained dataset revisions on location contracts and document their [coverage and pinning limits](../../plugins/location-providers/#define-revision-coverage). An omitted revision means unversioned: Lyra can reuse matching contents without guaranteeing freshness.

Change the optional run-wide `cache_revision` for data changes outside declared coverage or for unversioned implementation changes. Lyra includes it alongside location revisions and computation identity for every node. A fresh value preserves earlier entries, and subsequent runs with that value can reuse results. You manage invalidation and cleanup.

## Enable persistent caching

Enable caching in [server configuration](../../operate/configuration/#cache-metric-tables) or [local execution](../../use/local-execution/#reuse-results-across-runs). If cache storage fails, Lyra logs a warning and continues execution; later requests may repeat the calculation.
