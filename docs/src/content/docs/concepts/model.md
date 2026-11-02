---
title: How metrics and plugins fit together
description: Understand the vocabulary used by the catalog, plugin SDK, and execution graph.
contentType: Conceptual
contentPlan: concepts/model
sidebar: { label: "Metrics And Plugins" }
---

Use metrics to read source data, transform tables, or calculate results. Plugins register these functions as public capabilities or private calculation dependencies.

## A metric declares a calculation

A metric accepts declared inputs and returns declared tables. The public catalog groups variants into [families and editions](#names-identify-different-things). Inspection describes the inputs, sources, and meaning of each output row. Lyra excludes metrics marked `private=True` from discovery and direct submission, while allowing other metrics to use them as dependencies.

For example, a tract population family can offer named census editions and accept a state ID selection. [Canonical operations](../../plugins/location-operations/#offer-canonical-public-selection), which authors mark for automatic selection, select the tracts; the metric still returns its declared tract rows. Aggregating those rows to state totals requires a calculation that explicitly declares that meaning.

The metric’s Python function performs the calculation. Its parameter model describes ordinary choices, and spatial annotations describe region inputs.

## Metrics supply tables to other metrics

A metric can read source data or transform a table from another metric. Use dependencies when you want to reuse a metric’s output in another calculation.

A metric declares the tables it needs as dependencies. During execution, Lyra runs their producers and passes the resulting tables to the metric’s local function arguments. Public metrics can run directly; both public and private metrics can supply tables to another metric.

Your plugin determines the data sources and scientific choices, including how it transforms geometry and calculates results.

## Providers resolve IDs and operations derive locations

A [location provider](../../plugins/location-providers/) resolves exact IDs within a declared collection and unit type. A location operation selects or constructs a fully resolved `Locations` value for another graph invocation. Both operations and metrics can consume location arguments and table dependencies.

Public requests name a metric family and supply named ID selections. Select an edition when the family requires one. Canonical operations convert accepted source types into native inputs: the location contracts the metric declares.

Explicit dependencies define the relationships that the calculation needs. Your plugin implements those relationships and their spatial algorithms. Constructing derived locations does not publish a provider collection. [Derive locations inside a graph](../../plugins/location-operations/) demonstrates street selection for blocks.

## A plugin registers nodes

A plugin is an installable Python package with a factory returning a `PluginDefinition`. The definition lists its metrics, location operations, and resources. Other plugins can depend on these metrics’ outputs.

The package also includes a generated manifest. It describes the contract without requiring the application programming interface (API) server to import calculation code. Python definitions are the authoring source; the manifest is their generated deployment artifact.

## Names identify different things

A distribution, node, table, and column each have their own name. Keeping these roles separate helps you connect dependencies and interpret results:

| Name | Example | Role |
| --- | --- | --- |
| Distribution | `lyra-smoke-plugin` | Installed Python package |
| Node | `example_population_baseline` | Calculation or acquisition function |
| Output | `example_population_areas` | A whole table produced by a node |
| Dependency input | `records` | Local argument receiving an upstream table |
| Column | `population` | A value within a table |

A family identifies variants with a shared structural and parameter contract. Named editions choose concrete bindings; an implicit variant needs no edition selector. Equal schemas alone do not associate nodes into a family.

Node names are unique across enabled plugins. Output names are local to each producer. Local dependency argument names and column names serve a narrower scope.

## A job is one server execution

Submitting a metric creates a job with an ID. The job runs the metric and its dependencies, then retains status and a terminal result for a configured period.

A local runner executes the same definitions in your process and returns tables directly. It does not create a retained server job.

Continue to [graph execution](../graphs/), [the job lifecycle](../lifecycle/), or [architecture](../architecture/) for the next level of detail.
