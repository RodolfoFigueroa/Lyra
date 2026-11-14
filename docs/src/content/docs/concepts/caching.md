---
title: How persistent caching works
description: Understand when Lyra reuses stored results and which changes require new declared identities.
contentType: Conceptual
contentPlan: concepts/caching
sidebar: { label: "How Persistent Caching Works" }
---

Lyra saves final outputs from local runs and worker jobs in SQLite. A saved result doesn’t prove that Lyra skipped a calculation: each node’s cache policy controls reuse. Caching defaults to `off`; `full` and eligible `per_location` declarations allow reuse when computation identities match.

## Reuse tables across executions

Lyra prepares parameters and resolves location inputs before checking for stored results. Resolving inputs can require provider acquisition and operation execution. When a metric finds matching results, it skips its handler and remaining table dependencies. An operation restores its saved mapping, then resolves the selected target records.

This reuse is a cache hit. Use [execution observations](../../use/results/#inspect-execution-provenance) to distinguish cache hits from completed calculations.

A computation key identifies the immutable values a calculation produces. Lyra derives the key from:

- Value kind and implementation identity `(name, code_revision)`
- [Output scope](../../plugins/outputs/#declare-location-ownership), computation scope, and the primary-input name for location-scoped metrics
- Complete effective parameters, including defaults and nulls
- All named input identities, including provider scopes and upstream computation keys
- Direct origin identities for metrics, or the target provider scope for operations

A [provider scope](../../plugins/location-providers/#define-revision-coverage) combines the provider ID, code revision, and origin identities. The scope and `location_id` identify each record. Identical IDs from different scopes identify different records, and empty selections still retain their provider scope.

Inputs from an operation also retain that operation’s key and its source/target relationships. Resolving target records doesn’t remove those dependencies from the receiving computation’s identity.

Reordering location selections or origins doesn’t change identity; reordering parameter lists does. Omitting a parameter and supplying its default produce equal identities. Named inputs distinguish roles, even when they use the same provider.

A shared input contains its complete bound selection. Each dependent partition includes that selection in its identity, so changing shared context changes the dependent keys.

### Complete and per-location computations

The cache policy determines whether a key identifies a complete computation or one location’s computation. Both `off` and `full` cover the complete selection initially bound to the calculation. Selections A/B/C and A/B/D have different keys. A consumer selecting only A still depends on the complete upstream computation.

`per_location` assigns a key to each primary location, qualified by its provider scope. That key includes applicable dependencies and complete shared inputs. Operations partition by source location; metrics partition by the locations in their primary input. Running several partitions in one handler call doesn’t change their keys.

Lyra reuses stored results when every requested computation key is present. If some keys are missing, Lyra can still execute the entire bound selection. A cache hit can also require preparation and acquisition.

## Store and validate cached results

SQLite stores immutable typed rows under computation or provider-record keys. Lyra stores operation source/target pairs separately from provider records. It saves every output and the applicable partition completion together in one transaction after successful validation.

A successful empty output retains its schema and completion status. Failed or unprocessed work never counts as a completed empty result.

When reading stored values, Lyra reconstructs declared types and geometry CRS, including empty and all-null columns. Cache reads also validate outputs against the registered producer schema. Damaged required data causes an integrity error; lookup doesn’t fabricate an empty result or rerun a handler to repair it.

Writing the same key again preserves its existing value. An invoked `off` node executes even when its value is retained. Disabling caching preserves stored values, while repeated references within one graph can share that run’s validated results.

## Keep cached results current

Your plugin must return the same logical values and membership for the same identity and effective inputs, even with caching off. Represent differences that affect outputs through declared identities or effective parameters.

The optional `code_revision` token identifies an implementation. Omission and `None` have the same stable identity. Lyra preserves nonblank string tokens exactly: `"2"` and `"02"` differ.

Update the relevant declaration when outputs change:

- Change the node ID or `code_revision` when declarations, helpers, or callbacks change outputs
- Change an operation’s revision when either its target selector or mapper changes outputs
- Change an origin identity when external data changes
- Change a provider identity when retrieval, transformation, schemas, or collection membership change

An unversioned origin still promises fixed contents. Lyra trusts your declarations and doesn’t detect changes by inspecting code, comparing outputs, or reading package versions. Operational metadata, such as credentials or job IDs, doesn’t contribute to computation keys.

Lyra accepts the optional request field `cache_revision` and records it in request metadata. This field doesn’t change stored-value keys or force recomputation. Use declared revisions and effective parameters to identify changes that affect outputs.

Recreate runners and restart deployed processes after changing definitions. Processes sharing a store must honor the same immutable identities. [Provider revision coverage](../../plugins/location-providers/#define-revision-coverage) explains these responsibilities.

## Retain stored results

Local runs automatically use `.lyra/results.sqlite3`; set `result_store_path` to choose another file. The API and workers use one [shared SQLite path](../../operate/caching/) on one host.

Every successful managed execution receives a distinct final-result reference, including development runs, empty results, and cache hits. Several references can select the same stored values without copying rows. Lyra retains required final outputs and their retrieval descriptions; it writes intermediates when caching or ordinary retrieval needs them.

Lyra retains everything it writes indefinitely. Job expiration, changed keys, and cache-policy changes don’t delete stored data. Keep the local store path and result reference together to [reopen a result](../../use/results/#reopen-a-local-result). Remote methods accept job IDs, whose availability follows the server’s job retention setting.
