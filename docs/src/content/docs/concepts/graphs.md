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

The canonical conversion graph can contain cycles, but each accepted execution plan must be free of cycles. Discovery lists supported sources for each input; Lyra validates their combination when you submit a request.

During request preparation, Lyra checks the selected routes and their calculation dependencies for execution cycles. Lyra rejects invalid combinations before queueing the job or acquiring data.

Workers check the accepted plan’s execution order again before acquiring data. During execution, Lyra checks actual arguments, native return shapes, and public serialization.

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

Enable persistent caching when later runs should reuse validated results. [How persistent caching works](../caching/) explains identity, revisions, validation, and concurrency. A cache hit may still require input acquisition and preparation.

## Stop at the first failure

A failed invocation stops the graph. The job returns no partial result and does not retry automatically. Diagnostics identify the failing node and dependency path.

Lyra never caches failed invocations. Successful dependencies can remain cached when a later calculation fails, so a new submission can reuse them.

Use [plugin tests](../../plugins/testing/) to verify forwarding and reuse with fake sources. See [troubleshooting](../../operate/troubleshooting/#diagnose-a-graph-failure) to investigate deployed failures.
