---
title: Understand selection and calculation dependencies
description: Explain how automatic routes connect public selections and dependency inputs without changing the calculation’s output meaning.
contentType: Conceptual
contentPlan: concepts/selection
sidebar: { label: "Understand Selection And Calculation Dependencies" }
---

Public selection determines the native locations supplied to a metric. Explicit dependencies determine the calculation the metric performs. The city example uses both mechanisms while keeping result meaning visible.

## Convert accepted public selections

`city_street_maintenance` calculates one row per street and accepts block selections through a [canonical operation](../../plugins/canonical-selection/). The operation preserves each block/street pair, including streets that belong to both blocks. The street calculation then deduplicates street IDs so it measures each street once.

The request supplies IDs, not operation names or a pipeline. The catalog fixes a route before acquisition or enqueueing. A native contract match requires zero operations. The accepted route stays fixed during execution.

## Declare methodology explicitly

`city_block_maintenance` derives streets internally, invokes the street calculation, and aggregates with an explicit membership table. Its output contains block rows because that handler performs the aggregation. Weights and relationships remain plugin-owned data.

Each dependency input declares the provider it requires. Its [binding](../../plugins/dependencies/#4-forward-the-required-spatial-inputs) selects locations from the calling metric. For example, a block metric can supply blocks to a dependency that requires streets; canonical routing converts the selection. Nested `Input` references still read the inputs of the metric that declares the binding.

An explicit operation keeps its fixed target provider. Lyra can add canonical routes before its source or between its target and the next dependency input. Matching [provider scopes](../../plugins/location-providers/#define-revision-coverage) need no conversion; missing or ambiguous routes fail before acquisition. The receiving metric’s [primary input](../../plugins/outputs/#declare-location-ownership) determines output ownership, while your calculation performs any aggregation.

Follow [the worked comparison](../../plugins/tutorial/canonical/) or [canonical selection rules](../../plugins/canonical-selection/) for eligibility, shortest routes, and ties.
