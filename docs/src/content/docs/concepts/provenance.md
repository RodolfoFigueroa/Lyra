---
title: Understand execution provenance
description: Distinguish accepted bindings, resolved inputs, and observed execution without assuming replayability.
contentType: Conceptual
contentPlan: concepts/provenance
sidebar: { label: "Understand Execution Provenance" }
---

Use provenance to distinguish what Lyra accepted from what it observed during execution. Results identify the public metric and selected edition alongside output metadata. The descriptor records these details:

- Validated original selections
- Effective parameters, including defaults
- Concrete contracts and the canonical route for each input
- Pinned revisions, including bindings without a revision
- The optional run-wide cache revision

When Lyra observes native inputs, it records their counts and content fingerprints. These fingerprints identify the resolved contents for comparison without embedding every intermediate table.

Read accepted bindings separately from execution observations. A planned dependency may not have executed; Lyra marks cached results as reused. When Lyra reuses a result, it records this run’s selections and route.

Matching schemas do not make source observations equivalent. Keep source meaning, field definitions, observation periods, releases, and geographical editions distinct when interpreting results.

Provenance does not create replayable snapshots or guarantee historical source availability. For local runs, inspect provenance separately with `runner.inspect()` after retrieving native tables.

Use the executable contract and result metadata to interpret a result. Reserve [dependency inspection](../../use/mcp/#inspect-calculation-relationships) for questions about the calculation’s relationships.
