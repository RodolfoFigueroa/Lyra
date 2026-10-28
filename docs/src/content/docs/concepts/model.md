---
title: Metrics, datasets, and plugins
description: Understand the vocabulary used by the catalog, plugin SDK, and execution graph.
contentType: Conceptual
contentPlan: concepts/model
sidebar: { label: "Metrics, Datasets, And Plugins" }
---

Lyra connects your calculation to the people and applications that need to run it. Metrics describe public calculations, datasets acquire their source tables, and plugin packages register those functions together.

## A metric is a public calculation

A metric accepts declared inputs and returns declared tables. The catalog publishes its name, description, input schema, and output metadata so a caller can decide how to use it.

For example, a population metric could accept a region and a census year. Its outputs might be a table of source areas and a table containing their total.

The metric’s Python function performs the calculation. Its parameter model describes ordinary choices, and spatial annotations describe region inputs.

## A dataset supplies a table

A dataset acquires source data or transforms it into an intermediate table. It can query a database, read a file, or use an external service.

A metric declares the tables it needs as dependencies. During execution, Lyra runs their producers and passes the resulting tables to the metric’s local function arguments. Dataset nodes are graph dependencies; they are not public execution roots.

This keeps source acquisition explicit. The plugin still owns the scientific choices: sources, coverage, reprojection, joins, and formulas.

## A plugin registers nodes

A plugin is an installable Python package with a factory returning a `PluginDefinition`. The definition explicitly lists its metric and dataset functions. A package can contain only datasets if it exists to supply other plugins.

The package also includes a generated manifest. It describes the contract without requiring the API to import calculation code. Python definitions are the authoring source; the manifest is their generated deployment artifact.

## Names identify different things

A distribution, node, table, and column each have their own name. Keeping these roles separate helps you connect dependencies and interpret results:

| Name | Example | Role |
| --- | --- | --- |
| Distribution | `lyra-smoke-plugin` | Installed Python package |
| Node | `example_population_baseline` | Calculation or acquisition function |
| Output | `example_population_areas` | A whole table produced by a node |
| Dependency input | `records` | Local argument receiving an upstream table |
| Column | `population` | A value within a table |

Node and output names are unique across enabled plugins. An output can share its own producer’s node name. Local dependency argument names and column names serve a narrower scope.

## A job is one server execution

Submitting a metric creates a job with an ID. The job runs the metric and its dependencies, then retains status and a terminal result for a configured period.

A local runner executes the same definitions in your process and returns tables directly. It does not create a retained server job.

Continue to [graph execution](../graphs/), [the job lifecycle](../lifecycle/), or [architecture](../architecture/) for the next level of detail.
