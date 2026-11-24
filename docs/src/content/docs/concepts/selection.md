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

Your request supplies the provider ID and record IDs. Lyra chooses the route before it queues the job or reads provider data, then uses that route throughout execution.

## Declare methodology explicitly

`city_block_maintenance` derives streets internally, invokes the street calculation, and aggregates with an explicit membership table. Its output contains block rows because that handler performs the aggregation. Weights and relationships remain plugin-owned data.

Each dependency input declares the provider it requires. Its [binding](../../plugins/dependencies/#4-forward-the-required-spatial-inputs) selects locations from the calling metric. For example, a block metric can supply blocks to a dependency that requires streets; canonical routing converts the selection. Nested `Input` references still read the inputs of the metric that declares the binding.

Lyra can route a selection to an explicit operation’s source provider, then route its results to the next input’s provider. When the provider IDs match, Lyra uses the records directly. It rejects missing or ambiguous routes before reading provider data.

The receiving metric’s [primary input](../../plugins/outputs/#declare-location-ownership) determines which records own its output rows. Your calculation performs any aggregation.

Follow [the worked comparison](../../plugins/tutorial/canonical/) or [canonical selection rules](../../plugins/canonical-selection/) for eligibility, shortest routes, and ties.
