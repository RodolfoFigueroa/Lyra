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

Each node has its own inputs. Dependencies do not add hidden public request fields. Contexts and dependency tables are supplied by the runtime and excluded from the public request schema.

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

Identical producer invocations reuse their native tables within a job, including all outputs of a multi-output producer. Separate jobs acquire their inputs independently.

Treat dependency tables as read-only because Lyra passes them by reference, without enforcing immutability. If your calculation needs to change a received table, make a copy first so other consumers retain the original values.

The requested metric returns all its declared outputs. Intermediate tables remain internal.

## Stop at the first failure

A failed invocation stops the graph. The job fails without partial output publication or automatic replay. Diagnostics identify the failing node and dependency path.

A new submission begins a fresh execution. Use [plugin tests](../../plugins/testing/) to verify forwarding and reuse with fake sources, and [troubleshooting](../../operate/troubleshooting/#diagnose-a-graph-failure) to investigate deployed failures.
