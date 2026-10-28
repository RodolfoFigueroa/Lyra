---
title: How metrics and plugins fit together
description: Understand the vocabulary used by the catalog, plugin SDK, and execution graph.
contentType: Conceptual
contentPlan: concepts/model
sidebar: { label: "Metrics And Plugins" }
---

Use metrics to read source data, transform tables, or calculate results. Plugins register these functions so you can run each metric directly or combine them through dependencies.

## A metric is a public calculation

A metric accepts declared inputs and returns declared tables. The catalog publishes its name, description, input schema, and output metadata so a caller can decide how to use it.

For example, a population metric could accept a region and a census year. Its outputs might be a table of source areas and a table containing their total.

The metric’s Python function performs the calculation. Its parameter model describes ordinary choices, and spatial annotations describe region inputs.

## Metrics supply tables to other metrics

A metric can read source data or transform a table from another metric. Use dependencies when you want to reuse a metric’s output in another calculation.

A metric declares the tables it needs as dependencies. During execution, Lyra runs their producers and passes the resulting tables to the metric’s local function arguments. Every registered metric can run directly or supply tables to another metric.

Your plugin determines the data sources and scientific choices, including how it transforms geometry and calculates results.

## A plugin registers nodes

A plugin is an installable Python package with a factory returning a `PluginDefinition`. The definition explicitly lists its metric functions. Other plugins can depend on these metrics’ outputs.

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
