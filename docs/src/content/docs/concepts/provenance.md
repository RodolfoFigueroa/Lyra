---
title: Understand execution provenance
description: Distinguish accepted bindings, resolved inputs, and observed execution without assuming replayability.
contentType: Conceptual
contentPlan: concepts/provenance
sidebar: { label: "Understand Execution Provenance" }
---

Use provenance to distinguish what you requested from what Lyra observed during execution. The [result descriptor](../../use/results/#inspect-before-downloading) separates the accepted job (`provenance.accepted`) from execution observations (`provenance.execution`).

## Read the accepted plan

The accepted job identifies the plugin and discovery catalog used for your request. It also records when Lyra accepted the job. Its `plan` contains:

- Validated original selections and supplied parameter values
- `request.supplied_fields`, which records which request fields you supplied
- Effective parameters: the values Lyra uses after applying defaults
- Concrete contracts and the [canonical route](../selection/) for each input
- Pinned data revisions, including bindings without a revision
- Your optional [cache revision](../caching/)

For example, omitting a parameter differs from supplying its default value. The effective values can match while the submitted requests remain different. Lyra retains the accepted plan when execution fails or reuses a cached result.

## Interpret execution observations

After an execution attempt, `provenance.execution` records the observations Lyra collected. If the job fails before execution starts, this field is `null`. If execution fails partway through, the record includes observations collected before the failure.

For resolved inputs, Lyra records counts and content fingerprints: identifiers you can compare without retrieving intermediate tables. Node observations distinguish completed calculations from cached results and results shared within the run.

A planned dependency may not execute if Lyra can reuse a result. The accepted plan still describes the current run’s selections and route.

## Inspect a local run

After a local run, call `runner.inspect()` to read its provenance. The returned record separates the accepted plan in `accepted` from observations in `execution`. Development runs on resolved inputs have no accepted public plan.

## Understand what provenance cannot establish

Matching schemas do not make source observations equivalent. When comparing results, check these source details:

- Meaning and field definitions
- Observation period and release
- Geographical edition

Provenance does not preserve historical source data or guarantee that those sources remain available. Use the captured contracts and result metadata to interpret a result. Use [dependency inspection](../../use/mcp/#inspect-calculation-relationships) to understand which calculations depend on others.
