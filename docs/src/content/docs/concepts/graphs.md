---
title: How dependency graphs execute
description: Understand ordering, parameter forwarding, table reuse, and failures within a metric execution.
contentType: Conceptual
contentPlan: concepts/graphs
sidebar: { label: "How Dependency Graphs Execute" }
---

When a metric needs tables from other nodes, those dependencies form a directed graph. Lyra validates the graph before execution, then runs the required nodes in dependency order so each consumer receives its inputs.

For an authoring example, see [Connect metrics and dependencies](../../plugins/dependencies/).

## Start at the requested metric

The public request names one metric. Lyra prepares its parameters and spatial inputs, then follows its declared dependencies.

Each node has its own inputs. Dependencies do not add hidden public request fields. The runtime supplies contexts and dependency tables separately from the public request.

A dependency refers to a named output. Several inputs can consume the same producer, and a producer can publish several tables.

## Validate the complete graph

Registration and manifest generation check local declarations and signatures. They can run without installing referenced implementations or accessing sources.

Loading enabled manifests checks the combined graph: referenced outputs, name collisions, cycles, parameter paths, and required spatial forwarding. Execution then checks actual arguments, native return shapes, and public serialization.

These stages answer different questions. A valid local declaration does not prove that all dependency packages or source services exist in a deployment.

## Run sequentially on one worker

For server jobs, the requested metric’s queue selects the worker that executes the whole dependency graph. Dependencies execute sequentially on that worker, even when their metrics use different queues for direct requests.

That worker needs every plugin and source client required by the graph.

Local execution follows the same dependency structure in the caller’s process.

## Reuse tables within one execution

Identical producer invocations reuse their native tables within a job. This reuse includes every output from a producer that returns several tables.

Treat dependency tables as read-only because Lyra passes them by reference, without enforcing immutability. If your calculation needs to change a received table, make a copy first so other consumers retain the original values.

The requested metric returns all its declared outputs. Intermediate tables remain internal.

## Reuse tables across executions

Persistent caching lets later jobs reuse validated tables from earlier jobs. When Lyra finds the requested metric in the cache, it skips that metric’s calculation and its dependencies.

Lyra identifies a cached invocation by:

- Metric name
- Normalized parameters, including defaults and validated values
- Resolved features, including IDs, geometries, properties, and coordinate reference system (CRS)
- Each input’s collection and unit type under the producer’s argument name

Suppose metric `B` depends on metric `A`. A job calculates `A` with `year=2025`. A later job calculating `B` reuses those tables if its dependency requests `A` with the same effective inputs. Parameters used only by `B` do not affect reuse of `A`.

Each cache entry contains every output from one invocation. Lyra publishes those outputs together and validates cached tables before returning them. Filesystem locks prevent simultaneous duplicate calculations among processes sharing a functioning cache on one host.

Cached results must depend only on declared inputs and resource data that stays unchanged. Processes sharing a cache directory must use equivalent implementations and resource settings. You manage invalidation and cleanup because Lyra does not detect code or source changes.

Enable caching in [server configuration](../../operate/configuration/#cache-metric-tables) or [local execution](../../use/local-execution/#reuse-results-across-runs). If cache storage fails, Lyra logs a warning and continues execution; later requests may repeat the calculation.

## Stop at the first failure

A failed invocation stops the graph. The job returns no partial result and does not retry automatically. Diagnostics identify the failing node and dependency path.

Lyra never caches failed invocations. Successful dependencies can remain cached when a later calculation fails, so a new submission can reuse them.

Use [plugin tests](../../plugins/testing/) to verify forwarding and reuse with fake sources. See [troubleshooting](../../operate/troubleshooting/#diagnose-a-graph-failure) to investigate deployed failures.
