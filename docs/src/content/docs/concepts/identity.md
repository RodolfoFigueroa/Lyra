---
title: Understand sources, editions, and revisions
description: Distinguish unit identity, source observations, concrete metrics, and cache invalidation.
contentType: Conceptual
contentPlan: concepts/identity
sidebar: { label: "Understand Sources, Editions, And Revisions" }
---

A schema describes structure. Source contracts describe which observations and geographical units a calculation uses. These identities answer different questions even when their tables share columns.

| Choice | Meaning | City example |
| --- | --- | --- |
| Collection and unit type | Which kind of entity IDs select | `example:city` / `street` |
| Geographical edition | Which boundaries and unit identities apply | `layout_v1` |
| Source contract | Which observation provides geometry and attributes | `bundled-survey` |
| Metric name | Which registered calculation runs | `city_street_maintenance` |
| Data revision | Which maintained correction/version of a concrete dataset applies | `fixture_1` covers street geometry and condition observations |
| `cache_revision` | A caller-controlled run-wide identity component | A value for changes outside declared coverage |

## Choose metric names and parameters

Use ordinary parameters for choices a handler supports, such as the city’s condition threshold. Register each calculation variant under its own metric name. Each metric declares its parameters, named inputs, and outputs.

Equal IDs across geographical editions establish no correspondence. Matching schemas establish neither equivalent observations nor statistical comparability. A source edition, observation period, release, and geographical edition remain separate metadata.

## Maintain revision coverage

The city street revision covers geometry and condition observations. Its block revision covers geometry and membership. Advance the appropriate revision when covered data changes. A revision does not create a storage snapshot; a provider must honor a pinned revision or fail.

Use `cache_revision` for uncovered changes or deliberate recomputation. It adds to declared revisions and preserves earlier cache entries. Use these guides for the relevant declarations and behavior:

- [Persistent caching](../caching/): cache reuse
- [Provider contracts](../../plugins/location-providers/): source declarations
- [Register concrete metrics](../../plugins/metrics/): calculation names and dependencies

## Distinguish discovery from computation identity

Lyra uses three identities for different decisions:

| Identity | Purpose |
| --- | --- |
| Discovery fingerprint | Identifies public catalog metadata, including descriptions |
| Deployment `build_revision` | Checks whether API and workers use the same immutable build |
| [Computation identity](../caching/#keep-cached-results-current) | Determines whether a calculation can reuse cached results |

The build revision covers executable code, declarations, and routing policy. Change it whenever those contents change, including edits to declaration descriptions. API and workers must share the same build and revision.

A worker compares its build revision with the revision in the queued request before acquiring resources or calling handlers. A mismatch fails the job with `deployment_changed`; you can resubmit against the current catalog. A compatible worker prepares the request against its live definitions. The executor then validates the complete plan before acquiring resources.

For computation identity, Lyra combines the author’s `(name, code_revision)` with semantic contracts and dependencies. Build revisions and discovery fingerprints do not invalidate cached results. Editing only a description therefore preserves cache reuse, although deploying that edit requires a new build revision.
