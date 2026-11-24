---
title: How dependency graphs execute
description: Understand ordering, parameter forwarding, table reuse, and failures within a metric execution.
contentType: Conceptual
contentPlan: concepts/graphs
sidebar: { label: "How Dependency Graphs Execute" }
---

Metrics produce tables; [location operations](../../plugins/location-operations/) map source IDs to existing records in a fixed target provider. Table dependencies and [location bindings](../../plugins/dependencies/#4-forward-the-required-spatial-inputs) form one directed graph. Lyra validates this graph before execution and runs each dependency before the calculation that needs it.

Handlers process whole location sets. Your plugin defines relationships, joins, spatial algorithms, and which features appear in the output. For authoring examples, see [Connect metrics and dependencies](../../plugins/dependencies/) and [Derive locations inside a graph](../../plugins/location-operations/).

## Start at the requested metric

Your request selects one public metric by its registered name. Before acquiring data, Lyra validates the request and records its routes and data revisions. A [canonical route](../../plugins/location-operations/#offer-canonical-public-selection) converts a source selection into the metric’s native location contract.

Lyra resolves the requested metric’s inputs, then executes its dependencies. A dependency input also needs a canonical route when its bound source differs from its required provider. This can require another conversion after an explicitly bound operation. Routing preserves the providers you declare and the metric’s [output ownership](../../plugins/outputs/#declare-location-ownership).

Each node has its own inputs. Dependencies do not add hidden public request fields. The runtime supplies contexts and dependency tables separately from the public request.

A dependency refers to a named output. Several inputs can consume the same producer, and a producer can publish several tables.

## Validate the complete graph

Registration and live inspection check local declarations and signatures. They can run without installing referenced implementations or accessing sources.

When compiling enabled live definitions, Lyra checks the combined graph for:

- Valid output and operation references
- Name collisions
- Cycles across table dependencies and location bindings
- Valid parameter paths and required concrete location bindings
- Shared family structures and ordinary parameter contracts
- Satisfiable canonical operations, shortest-route preferences, and unambiguous accepted source contracts

The canonical conversion graph can contain cycles, but each accepted execution plan must be free of cycles. Discovery lists supported sources for each input; Lyra validates their combination when you submit a request.

During request preparation, Lyra checks the selected routes and their calculation dependencies for execution cycles. Lyra rejects invalid combinations before queueing the job or acquiring data.

The executor validates the complete prepared plan before acquiring resources or invoking handlers, in both local and worker execution. During execution, Lyra checks actual arguments, native return shapes, and public serialization.

These stages answer different questions. A valid local declaration does not prove that all dependency packages or source services exist in a deployment.

## Run sequentially on one worker

The API queues the requested metric and its dependencies as one job. The next available worker runs the whole graph, executing each dependency before the metric that uses its output. Every worker therefore needs the plugins and source clients for every graph the deployment accepts.

Local execution follows the same dependency structure in the caller’s process.

## Reuse tables within one execution

Identical metric invocations reuse their native tables within a job. Identical location operation invocations reuse their validated mapping groups. This reuse includes every output from a producer that returns several tables.

Each handler receives an independent frame for each table argument. You can edit these frames without changing another argument or consumer. Lyra keeps its own records and checks output owners against the original selections. Lyra also copies returned tables so producer and caller edits cannot change retained results.

The requested metric returns all its declared outputs. Intermediate tables remain internal.

## Reuse tables across executions

Enable a node’s cache policy to allow reuse across runs. Lyra saves required final outputs automatically, regardless of cache policy. [How persistent caching works](../caching/) explains when reuse skips a handler and what preparation still requires.

## Stop at the first failure

A failed invocation stops the graph. The job returns no partial result and does not retry automatically. Diagnostics identify the failing node and dependency path.

Lyra never caches failed invocations. Successful dependencies can remain cached when a later calculation fails, so a new submission can reuse them.

Use [plugin tests](../../plugins/testing/) to verify forwarding and reuse with fake sources. See [troubleshooting](../../operate/troubleshooting/#diagnose-a-graph-failure) to investigate deployed failures.

Metric publication commits all newly computed partitions from one successful
invocation together, including every named output, completion record, and retained
input binding. If publication fails, none of that invocation's new metric values
remain. Previously committed partitions and successful dependencies remain reusable.
Each partition keeps its own computation identity, so overlapping requests can
reuse individual partitions. Captured inputs can remain stored after a failure;
they do not mark the failed metric as complete.
