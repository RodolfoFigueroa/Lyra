---
title: Understand execution reports and provenance
description: Explain what Lyra computed or reused, and distinguish saved reports from diagnostics for the latest attempt.
contentType: Conceptual
contentPlan: concepts/provenance
sidebar: { label: "Execution Reports And Provenance" }
---

Use an execution report to explain what Lyra computed or reused for your request. Lyra saves a complete `ExecutionReport` for each successful run through a local runner or server worker. For local and development runs, it attaches `result.report` before returning, so reading the report needs no storage lookup. You can also [reopen a saved report](../../use/results/#reopen-a-local-result) by execution ID.

Each successful execution has its own identity and report, even when it reuses previously stored values. Reports cover all [output scopes](../../plugins/outputs/#declare-location-ownership), including empty results and runs that reuse every result from the cache. Every report has `status="succeeded"` and a required `result_ref` identifying its saved final result.

Lyra must save the required values and publish the result description with the complete report before returning success. For a failed or interrupted attempt, inspect its exception and available diagnostics; Lyra doesn’t retain partial failure reports.

## Read the normalized request

Read `report.request` to see what Lyra accepted for your run.

| Field | Meaning |
| --- | --- |
| `metric` | Selected concrete root metric name |
| `inputs` | Caller selections keyed by input name, each with `provider` and `ids` |
| `parameters` | Complete effective root parameters, including defaults and nulls |
| `interventions` | The additions and removals you requested for each catalog, including changes outside your selection |

In each intervention set, `add` lists complete records and `remove` lists the IDs you asked to remove. Read `addition_schema` for the catalog’s record schema and CRS, even when you only requested removals.

The request preserves your complete selection when a [route](../selection/) converts it or cache reuse narrows the work. Inputs derived through operations appear in graph bindings rather than becoming caller inputs. At return, request parameters match the root node’s `parameters` and `result.metadata.parameters`.

Effective values are the parameters after Pydantic validation and defaults. Omitting a parameter and explicitly supplying its default produce the same effective values. [Validators](../../reference/parameters/) must be deterministic and preserve normalized values on repeated validation without performing input/output (I/O). Workers revalidate those values before execution.

Reports describe those effective values without recording whether you supplied each field or accepted its default. They don’t retain raw parameter values or separate origins for nested fields and list elements.

## Follow resolved uses and bindings

A resolved use is one use of a [node definition](../model/) with particular inputs and effective parameters. `report.nodes` contains one summary per resolved use. Several consumers of that use share its summary.

For example, a comparison metric might use `block_area` for both selected blocks and reference blocks. Both uses have `node_id="block_area"`, but their distinct selections give them different `use_id` values.

Use IDs are local to a report. They are not retrieval references or stable identities across executions. Follow the root and its named inputs through ordinary attributes:

```python {2-3,6}
report = result.report
uses = {node.use_id: node for node in report.nodes}
root = uses[report.root_use_id]

for name, binding in root.inputs.items():
    producer = uses[binding.source_use_id]
    print(name, producer.node_id, binding.output_name, binding.provider_scope)
```

| Field | Meaning |
| --- | --- |
| `node.node_id`, `node.kind` | Definition name and kind: `metric`, `operation`, or `provider` |
| `node.parameters` | This use’s complete effective parameter values |
| `node.primary_input` | Primary location input name, an operation’s source name, or `None` for an unpartitioned metric |
| `node.inputs` | Named resolved bindings, including the selected route’s producing uses |
| `binding.source_use_id` | Producing use in this report |
| `binding.output_name` | Named metric output, or `None` for location records |
| `binding.provider_scope` | Concrete location provider scope, or `None` for a metric-table dependency |
| `node.outputs` | Named metric output descriptions with `scope`, `table_schema`, and `owner_scope` |

A [provider scope](../caching/#reuse-tables-across-executions) identifies a dataset through its provider ID, code revision, and origin identities. For an operation-derived location input, the scope describes the forwarded target records. For a metric table, find ownership in the producer’s named output description. Empty location-scoped outputs retain that ownership; unpartitioned outputs have `owner_scope=None`.

When you add or remove records, the effective scope identifies the dataset your run uses. Read `base` for the original provider scope and `intervention` for the set’s catalog, ID, and revision.

Lyra derives these descriptions from resolved bindings and declarations, so they remain available when execution skips or reuses work. Reporting doesn’t acquire records or save unnecessary intermediate tables. Editing a returned frame’s attributes cannot rewrite the descriptions.

## Follow named parameters

Read `report.parameter_bindings` to follow a parameter from one use to another. Each entry identifies both ends of a direct connection:

- `source_use_id` and `source_parameter`: sending use and parameter name
- `target_use_id` and `target_parameter`: receiving use and parameter name

Suppose A forwards `search_radius` to B’s `radius`, and B forwards it to C’s `distance`. The report records two bindings, with the effective values on all three uses.

Explicit `Input(...)` bindings and automatic matching-name forwarding use the same record format. Literal values and receiving-model defaults appear in `node.parameters` without a forwarding record. Equal values alone don’t establish forwarding.

Passing a nested object or list creates one binding for the complete parameter. Bindings describe the selected plan even when execution skips either use.

## Interpret the four outcomes

Read `node.outcome` to distinguish computation or data acquisition from reuse:

| `node.outcome` | Observed work for this use |
| --- | --- |
| `executed` | Performed computation or acquisition without reusing values |
| `reused` | Reused needed values without executing its own work |
| `mixed` | Both executed work and reused values |
| `skipped` | Needed neither execution nor value reuse |

For example, a metric reusing blocks A and B while computing D has outcome `mixed`. If a shared dependency executes for one consumer, another consumer bypassing it doesn’t make the dependency globally `skipped`. Reusing a downstream result can bypass a node whose own caching is off.

A handler that runs and returns an empty table is still `executed` when it reused nothing. A use with no requested partitions, invocation, or reuse is `skipped`. Both can belong to a successful empty result.

These outcomes summarize current work without counts or event histories for individual rows or partitions. They don’t reconstruct which historical execution produced cached values. Execution IDs and build revisions don’t change [cache identity](../caching/#reuse-tables-across-executions).

Even when a metric reuses every result or skips a catalog, Lyra still validates each intervention set. Find the set by its `scope` in `report.intervention_validations`, then read its `membership_lookup` value.

| `membership_lookup` | What Lyra did |
| --- | --- |
| `executed` | Queried the base dataset to confirm each added ID is new and each removal ID exists |
| `skipped` | Reused a successful validation for the same base dataset and intervention set |

Lyra reports the provider lookup as external acquisition. It excludes the records you supply and any work it does to load them into temporary tables.

## Read operation targets and stages

Read `node.operation` to distinguish an operation’s target from the steps that ran. Other node kinds have `operation=None`:

| Field | Meaning |
| --- | --- |
| `target_scope` | Effective scope of the catalog the operation selects from. The operation’s input binding identifies its immediate source. |
| `target_adapter` | Configured managed query adapter: `parquet`, `postgis`, or `None` |
| `selector` | Whether the selector constructed its target expression |
| `target_query` | Whether the provider executed target-query acquisition |
| `mapping` | Whether the mapping handler ran |

Compare the stage fields to see which steps ran:

| Situation | `selector` | `target_query` | `mapping` |
| --- | --- | --- | --- |
| Selector-backed operation computes missing values | `executed` | `executed` | `executed` |
| Its mapping is completely reused | `skipped` | `skipped` | `skipped` |
| Operation without a selector computes values | `not_applicable` | `not_applicable` | `executed` |

The mapping stage always applies. Selector and query stages use `not_applicable` only when the operation has no selector. A stage is `executed` if it ran at least once for this use. With partial reuse, the operation can have `outcome="mixed"` while all three stages execute for missing partitions.

Constructing an [Ibis target expression](../../reference/target-selection/) doesn’t acquire external data. Executing a query does, even when it returns zero candidates and stores no provider records. If the mapping handler receives those empty candidates, its stage is also `executed`.

To describe these stages, reports need neither query expressions nor credentials. They don’t store candidate membership solely to explain acquisition.

Read the provider’s outcome alongside the operation stages: forwarding a reused mapping can still require target-record acquisition. A configured target adapter or declared input alone doesn’t prove acquisition occurred. Lyra never constructs a selector or runs a query to enrich a report after cache reuse.

## Read revisions and direct origins

Use the declared revisions to identify the implementation and deployment behind a result. `node.code_revision` records the definition’s implementation revision, including `None`. `report.build_revision` separately identifies the [deployment build](../../operate/configuration/#assign-a-deployment-build-revision), or is `None` for local execution without one. The API and workers compare build revisions before acquisition; a mismatch fails the job with `deployment_changed`.

An origin identifies an external data source that a metric or provider declares it reads directly. Each entry in `node.origins` has these fields:

- `id`: source identifier
- `revision`: optional data revision; `None` identifies fixed, unversioned contents
- `description`: optional informational description

Lyra deduplicates origins by `(id, revision)`. Several providers can share an origin while retaining distinct provider scopes.

Consuming a provider’s records doesn’t add the provider’s origins to the metric’s direct origins. A metric that queries the same external source itself must declare it independently. Operations have `origins=[]`; their source bindings and `operation.target_scope` describe their data dependencies. Resources manage access and cleanup without defining source identity.

Lyra captures origins and revisions before initializing resources, so these declarations remain visible for reused and skipped uses. A declaration cannot prove which sources a metric handler actually accessed. Reports neither archive external sources nor guarantee replay against changed registrations. [Plugin authors](../../plugins/authoring/) must still declare complete dependencies and ensure deterministic calculations.

## Use transient inspection and failure diagnostics

Call `runner.inspect()` to diagnose the latest local attempt. It returns an independent copy with accepted metadata in `accepted` and observations in `execution`. These diagnostics include native input counts and `execution.succeeded`, plus labels for observed work:

- `started` or `completed`: invocation start or completion
- `cache_hit` or `partial_hit`: complete or partial reuse from storage
- `shared_reuse`: reuse within the current run

These diagnostic labels differ from `ExecutionReport` outcomes. Inspection is `None` before a run or after preparation fails. Failures after execution starts can leave observations collected before the error; a new attempt replaces the runner’s diagnostic state.

Remote results and inspections include job-scoped `provenance` describing the accepted request and available observations. `provenance.execution` can be `null` before execution starts or include partial observations after a failure. Access to these diagnostics depends on [job retention](../lifecycle/#job-access-has-a-retention-period).

Keep `result.report` to inspect a particular successful local execution. To read a report in another process or after job expiry, use [report lookup](../../use/results/#reopen-a-local-result) with the saved execution ID. Returned reports are ordinary Pydantic models: nested edits cannot change saved state, while field access makes no new copies.
