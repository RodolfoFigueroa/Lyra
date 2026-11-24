---
title: Understand provider IDs and dataset revisions
description: Select records by provider ID and identify the dataset revisions a calculation uses.
contentType: Conceptual
contentPlan: concepts/identity
sidebar: { label: "Provider IDs and Dataset Revisions" }
---

Select records by naming their provider and listing their record IDs. For example, `{"provider": "city_streets", "ids": ["street-1"]}` selects one street. The provider’s revisions identify which version of that street’s geometry and attributes your calculation uses.

| Choice | Meaning | City example |
| --- | --- | --- |
| Provider ID | Which dataset contains the records | `city_streets` or `city_blocks` |
| Origin ID and revision | Which release of the external data the provider uses | Both providers use origin `example-city` at revision `fixture_1` |
| Provider `code_revision` | Which revision of the provider implementation supplies the records | Both city providers declare `"1"` |
| Metric name | Which registered calculation runs | `city_street_maintenance` |

To select streets by block, use a metric that accepts `city_blocks`. The [canonical operation](../../plugins/canonical-selection/) `city_streets_for_blocks` reads the street IDs for each block. Lyra then resolves those IDs through `city_streets`.

## Choose metric names and parameters

Use ordinary parameters for choices a handler supports, such as the city’s condition threshold. Register each calculation variant under its own metric name. Each metric declares its parameters, named inputs, and outputs.

## Maintain revision coverage

A [provider scope](../../plugins/location-providers/#define-revision-coverage) identifies a fixed set of records and their contents. It includes the provider ID and code revision, along with each origin’s ID and revision. The city providers share an origin and code revision. Their different provider IDs give streets and blocks distinct scopes.

Update the city origin revision when you change the bundled street or block data, including block membership. During execution, each provider must resolve records from the scope Lyra selected during preparation or report an error.

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
