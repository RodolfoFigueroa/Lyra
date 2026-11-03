---
title: Understand selection and calculation dependencies
description: Explain automatic native-input selection separately from explicit calculation structure.
contentType: Conceptual
contentPlan: concepts/selection
sidebar: { label: "Understand Selection And Calculation Dependencies" }
---

Public selection determines the native locations supplied to a metric. Explicit dependencies determine the calculation the metric performs. The city example uses both mechanisms while keeping result meaning visible.

## Convert accepted public selections

`city_street_maintenance` calculates one row per street. Its canonical block-to-street operation lets callers submit blocks. Selection returns the union of related streets, each once, then the existing calculation runs on those native inputs.

The request supplies IDs, not operation names or a pipeline. The catalog fixes a route before acquisition or enqueueing. A native contract match requires zero operations. The accepted route stays fixed during execution.

## Declare methodology explicitly

`city_block_maintenance` derives streets internally, invokes the street calculation, and aggregates with an explicit membership table. Its output contains block rows because that handler performs the aggregation. Weights and relationships remain plugin-owned data.

Every nested `Input` reads the effective inputs of the metric or operation that declares the dependency. Supply compatible bindings for internal dependencies; public canonical routing does not change them or what each output row represents.

Follow [the worked comparison](../../plugins/tutorial/canonical/) or [canonical selection rules](../../plugins/canonical-selection/) for eligibility, shortest routes, and ties.
