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
| `code_revision` | An optional string identifying an implementation | `"1"` for a metric, operation, or provider |

## Choose metric names and parameters

Use ordinary parameters for choices a handler supports, such as the city’s condition threshold. Register each calculation variant under its own metric name. Each metric declares its parameters, named inputs, and outputs.

Equal IDs across geographical editions establish no correspondence. Matching schemas establish neither equivalent observations nor statistical comparability. A source edition, observation period, release, and geographical edition remain separate metadata.

## Maintain revision coverage

The city street revision covers geometry and condition observations; its block revision covers geometry and membership. Advance the appropriate revision when covered data changes.

A [provider scope](../../plugins/location-providers/#define-revision-coverage) fixes records and collection membership, including for an unversioned origin. The provider must honor the scope captured during preparation or fail.

Represent differences that affect outputs through declared identities or effective parameters. Change a node ID or its optional `code_revision` when declarations or callbacks change its output. An operation revision covers both its selector and mapper.

Omitting `code_revision` is equivalent to declaring `None`. Lyra preserves nonblank string tokens exactly. Use these guides for the relevant declarations and behavior:

- [Persistent caching](../caching/): cache reuse
- [Provider contracts](../../plugins/location-providers/): source declarations
- [Register concrete metrics](../../plugins/metrics/): calculation names and dependencies

## Identify a dataset with interventions

An effective dataset is the base dataset after your additions and removals. Its identity combines the base provider scope with the `(catalog, id, revision)` of your [intervention set](../../use/spatial-inputs/#prepare-changes-for-a-catalog). Reuse that combination only for the same changes.

For example, two proposals can add a street with the same ID and different lengths. Lyra stores the added street separately for each proposal. Base records keep their original keys in both runs.

Lyra includes the effective dataset identity in cache keys for calculations that use its records. For an operation, Lyra includes the target identity before running the selector. Two target revisions have distinct cache keys even when the selector returns the same IDs. Calculations independent of that catalog can still reuse their results.

## Distinguish discovery from computation identity

Lyra uses three identities for different decisions:

| Identity | Purpose |
| --- | --- |
| Discovery fingerprint | Identifies public catalog metadata, including descriptions |
| Deployment `build_revision` | Checks whether API and workers use the same immutable build |
| [Computation identity](../caching/#keep-cached-results-current) | Determines whether a calculation can reuse cached results |

The build revision covers executable code, declarations, and routing policy. Change it whenever those contents change, including edits to declaration descriptions. API and workers must share the same build and revision.

A worker compares its build revision with the revision in the queued request before acquiring resources or calling handlers. A mismatch fails the job with `deployment_changed`; you can resubmit against the current catalog. A compatible worker prepares the request against its live definitions. The executor then validates the complete plan before acquiring resources.

The [computation key](../caching/#reuse-tables-across-executions) combines `(name, code_revision)` with the calculation’s scopes and complete effective inputs. Build revisions and discovery fingerprints don’t invalidate cached results. Editing only a description preserves cache reuse, although deploying that edit requires a new build revision.
