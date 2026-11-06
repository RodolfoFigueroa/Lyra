---
title: How persistent caching works
description: Understand invocation identity, revision coverage, publication, validation, and reuse across runs.
contentType: Conceptual
contentPlan: concepts/caching
sidebar: { label: "How Persistent Caching Works" }
---

Persistent caching saves validated metric tables and location results for later executions. Lyra reuses these results when inputs and declared data revisions match. Processes sharing the cache must use equivalent code and resource settings.

## Reuse tables across executions

Lyra checks the cache the same way for local runs and queued jobs. Before each lookup, Lyra applies parameter defaults and resolves the node’s location inputs. Resolving those inputs can require running location operations and their table dependencies. If Lyra finds a matching result, it skips the node’s handler and remaining table dependencies.

Lyra identifies a cached invocation by:

- Node kind (metric or location operation) and name
- Normalized parameters, including defaults and validated values
- Resolved features, including IDs, geometries, properties, and coordinate reference system (CRS)
- Each input’s concrete source and geographic contract, including its edition, under the producer’s argument name
- Revisions of native location data and declared dependencies, including their own dependencies and an operation’s target dataset
- The node’s computation identity and optional run-wide `cache_revision`

Suppose metric `B` depends on metric `A`. A job calculates `A` with `year=2025`. A later job calculating `B` reuses those tables if its dependency requests `A` with the same effective inputs. Parameters used only by `B` do not affect reuse of `A`.

List order and receiving argument names affect cache identity. Caller names and execution paths do not. Lyra caches complete ordered batches; it does not guarantee reuse for individual IDs, subsets, or reordered inputs.

[Canonical selectors](../selection/) convert public selections to the metric’s native inputs before Lyra checks its cache. Selectors can also reuse cached selections. Different public selections can reuse a metric result when all cache inputs listed above match. Equal IDs alone are insufficient.

Revisions used only for public selection affect selector caches, but not the metric cache after native inputs match. Native dataset revisions and explicit calculation dependencies still invalidate the metric. A native revision change invalidates it even when returned values match.

A cache hit can still require source acquisition and intermediate artifact reads. Lyra records the current run’s original selection and accepted route in its [provenance](../../use/results/#inspect-execution-provenance). Execution observations distinguish cached results from newly executed nodes.

## Store and validate cached results

Each cache lookup references an immutable artifact: a set of files containing every output from one invocation. Lyra publishes these files together. Replacing or removing a lookup leaves the referenced artifact intact.

Lyra stores operation locations and named metric tables as uncompressed Arrow interprocess communication (IPC) files in versioned bundles. Constructing or caching locations does not register a provider collection.

The data reader verifies stored types and content fingerprints. Before reusing a result, the runtime checks it against the registered producer schemas and applies their declared type conversions. Empty results retain their declared columns and data types. An invalid cache entry triggers recomputation, while an invalid handler result fails execution.

## Coordinate concurrent calculations

Filesystem locks prevent duplicate calculations among processes sharing a functioning cache on one host. When a lookup finds no valid result, Lyra follows this sequence:

1. Finish the node’s dependencies.
2. Acquire the node’s cache lock and recheck the cache.
3. Reuse a valid result, or run the handler and validate its outputs.
4. Publish new results and release the lock.

Lyra finishes dependencies before acquiring the lock, so invocation locks do not nest.

## Keep cached results current

Processes sharing a cache directory must use equivalent implementations and resource settings. A computation identity determines which cached results Lyra can reuse. Lyra calculates this identity from:

- Protocol versions
- Node contracts, including validation rules and declared meaning
- Node dependencies, including their own dependencies
- Installed versions of packages containing those nodes
- Provider contracts used by location operations

Changes to descriptions preserve reuse, while changes to declared behavior or meaning invalidate relevant results:

| Declaration | Affects computation identity |
| --- | --- |
| Descriptions, schema titles, examples, descriptive edition metadata | No |
| Units, meaning, granularity, field definitions | Yes |
| Source observation periods and releases | Yes |
| Validation rules, defaults, literal dependency arguments | Yes |

Lyra preserves literal data when calculating identity, including keys named `description`. Changing such a value changes identity because the value is an input to the calculation.

An installed Python package, called a distribution, can contain several plugin nodes. Updating its version invalidates cached computations for its nodes and their dependents. This includes nodes whose code did not change. Changes to unrelated nodes or packages preserve reuse.

Lyra reads locally installed package versions. Publishing a release has no effect until you update the installed version. Lyra does not hash Python source.

Location operations can resolve provider data at runtime without declaring every lookup. Their identities therefore include the provider’s complete structural contract and installed version. Declared dataset revisions cover only the calculation’s declared contracts.

Lyra captures identities and installed versions when it compiles a catalog. Recreate local runners and restart deployed processes after changing declarations or installed packages.

Declare dataset revisions on location contracts and document their [coverage and pinning limits](../../plugins/location-providers/#define-revision-coverage). Without a declared revision, Lyra can reuse matching contents but cannot guarantee freshness.

Change `cache_revision` when data changes outside declared coverage or implementation changes lack a version update. This optional value affects every node’s cache identity while preserving earlier entries. Reuse the same value on subsequent runs to reuse their results. You manage invalidation and cleanup.

## Store and retain artifacts

Server caching uses the shared `artifact_dir`. For [local execution](../../use/local-execution/#reuse-results-across-runs), pass that argument to enable persistent caching. Both execution modes store validated tables or locations in immutable bundles. Computation lookups and retained jobs can reference the same bundle.

If cache access fails, Lyra logs a warning and can recalculate the result. A server job succeeds only after Lyra saves its final output artifact. [Offline pruning](../../operate/caching/#reclaim-artifact-storage) removes bundles only when neither retained jobs nor computation lookups reference them.
