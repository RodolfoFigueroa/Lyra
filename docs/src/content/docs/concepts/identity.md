---
title: Understand sources, editions, and revisions
description: Distinguish unit identity, source observations, metric editions, and cache invalidation.
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
| Metric edition | Which separately selectable calculation variant runs | A named survey variant with compatible inputs and outputs |
| Data revision | Which maintained correction/version of a concrete dataset applies | `fixture_1` covers street geometry and condition observations |
| `cache_revision` | A caller-controlled run-wide identity component | A value for changes outside declared coverage |

## Choose editions and parameters deliberately

Use ordinary parameters for choices a handler supports, such as the city's condition threshold. Use named metric editions for distinct source bindings or methodological variants with one shared structural and parameter contract. A family with named editions requires an explicit selection even when only one is installed.

Equal IDs across geographical editions establish no correspondence. Matching schemas establish neither equivalent observations nor statistical comparability. A source edition, observation period, release, and geographical edition remain separate metadata.

## Maintain revision coverage

The city street revision covers geometry and condition observations. Its block revision covers geometry and membership. Advance the appropriate revision when covered data changes. A revision does not create a storage snapshot; a provider must honor a pinned revision or fail.

Use `cache_revision` for uncovered changes or deliberate recomputation. It adds to declared revisions and preserves earlier cache entries. Read [persistent caching](../caching/) for exact reuse behavior, [provider contracts](../../plugins/location-providers/) for declarations, and [metric editions](../../plugins/editions/) for registration.
