---
title: Understand execution provenance
description: Distinguish accepted bindings, resolved inputs, and observed execution without assuming replayability.
contentType: Conceptual
contentPlan: concepts/provenance
sidebar: { label: "Understand Execution Provenance" }
---

Provenance records your request and Lyra’s execution observations. Use it to compare the accepted calculation with the work Lyra performed. The [complete result](../../use/results/#inspect-execution-provenance) separates the accepted job (`provenance.accepted`) from execution observations (`provenance.execution`).

## Read the accepted plan

The API saves the accepted job before queueing it. This record identifies the selected plugin, the discovery catalog, and the acceptance time. Its `plan` contains:

- Your validated location selections
- Effective parameters for the requested metric and each dependency or operation invocation
- [Location scopes](../../reference/locations/) and the [canonical route](../selection/) for each input
- Data bindings and their declared revisions; a binding may have no revision
- The [build revision](../../operate/configuration/#assign-a-deployment-build-revision), which identifies the deployment’s code, declarations, and routing policy
- Output and source descriptions for inspection
- Your optional [cache revision](../caching/)

Effective parameters include defaults, nulls, and values normalized by validators. Omitting a parameter and supplying its default produce the same effective values. Workers revalidate these complete values using the same build’s parameter models and reject requests with missing normalized values.

If the worker’s build revision differs from the accepted revision, the job fails with `deployment_changed` before resource acquisition. Submit a new job to use the current deployment. Lyra retains the accepted plan when execution fails or reuses a cached result.

## Interpret execution observations

The worker records what it observes during the run. The API returns those observations in `provenance.execution`. If the job fails before execution starts, this field is `null`. If execution fails partway through, the record includes observations collected before the failure.

For resolved inputs, Lyra records counts and content fingerprints. These fingerprints identify input contents without requiring you to retrieve intermediate tables. Node observations distinguish completed calculations from cached results and results shared within the run.

A planned dependency may not execute if Lyra can reuse a result. The accepted plan still describes the current run’s selections and route.

## Inspect a local run

After a local run, call `runner.inspect()` to read its provenance. The returned record separates the accepted plan in `accepted` from observations in `execution`. [Development runs](../../use/local-execution/#inspect-results-and-execution-details) accept the same ID selections and parameter mappings, including for private roots. Use `runner.inspect()` to read their prepared metadata.

## Understand what provenance cannot establish

Matching schemas do not make source observations equivalent. When comparing results, check these source details:

- Meaning and field definitions
- Observation period and release
- Geographical edition

Provenance does not preserve historical source data or guarantee that those sources remain available. Use the retained source descriptions and result metadata to interpret a result. Use [dependency inspection](../../use/mcp/#inspect-calculation-relationships) to understand which calculations depend on others.
