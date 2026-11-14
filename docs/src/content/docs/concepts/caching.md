---
title: How persistent caching works
description: Understand when Lyra reuses stored results and which changes require new declared identities.
contentType: Conceptual
contentPlan: concepts/caching
sidebar: { label: "How Persistent Caching Works" }
---

Lyra saves final outputs from local runs and worker jobs in SQLite. A saved result doesn’t prove that Lyra skipped a calculation: each node’s cache policy controls reuse. Caching defaults to `off`; `full` and eligible `per_location` declarations allow reuse when computation identities match.

## Reuse tables across executions

Lyra determines computation keys from declared identities before acquiring inputs. When every requested key has a saved value, Lyra can reuse the complete result: a cache hit. Use [execution observations](../../use/results/#inspect-execution-provenance) to distinguish cache hits from completed calculations.

A complete metric hit skips its handler and upstream work that the result no longer needs. An operation hit restores its source-to-target mapping without running its selector, candidate query, or mapping handler. Forwarding the selected targets can still require provider records.

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

For `per_location`, Lyra computes only primary partitions without saved values and combines them with retained partitions. Related dependencies supply groups for those missing primary locations. Independent reference inputs and unpartitioned dependencies remain complete.

An upstream producer with `off` or `full` caching keeps its complete original selection. The consumer can receive fewer groups without narrowing that producer’s computation.

## Choose a cache policy

Choose a cache policy on each declaration. Lyra accepts these settings and validates them before acquiring resources:

| Declaration | Accepted `cache` values | Default |
| --- | --- | --- |
| Metric with a primary location input | `"off"`, `"full"`, `"per_location"` | `"off"` |
| Metric without a primary input | `"off"`, `"full"` | `"off"` |
| Operation | `"off"`, `"full"`, `"per_location"` | `"off"` |
| Provider | `False`, `True` | `False` |

Mark exactly one metric location input `primary=True` to assign output rows to its locations. The marker doesn’t enable caching or promise independent computation. An operation’s sole source is automatically primary.

Each primary partition contains the output rows for one primary location. Choose `per_location` only if adding or removing other primary locations cannot change that partition’s result. This promise includes dependencies and complete shared context. Lyra trusts your declaration without running independence proofs.

Set a provider’s `cache = True` to reuse immutable records by exact ID. Providers reject strings and integers, and metrics and operations reject booleans.

Local and remote execution use the same declared policies, including `run_development()`. Supply selections and calculation parameters in your requests. When authoring a plugin, [update declared identities](#keep-cached-results-current) whenever outputs or collection membership change.

For an operation with a target selector, the independence promise covers selection and mapping together. A cache hit skips both callbacks and the candidate query, including when the saved mapping is empty. On a miss, Lyra queries the complete matching set, even when Lyra has cached individual records. Cached records cannot establish which records match a predicate.

## Overlapping selections

Overlapping requests can reuse the partitions they share. Consider a registered `block_area` metric with a primary `blocks` input and `cache="per_location"`. Starting with an empty store, request A/B, then B/C twice:

```python
first = runner.run(
    "block_area",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["A", "B"]}},
)
selection = {"blocks": {"provider": "city_blocks", "ids": ["B", "C"]}}
second = runner.run("block_area", inputs=selection)
third = runner.run_development("block_area", inputs=selection)
```

Lyra combines the handler’s output with retained partitions to return each complete selection:

| Run | Locations supplied to the handler | Locations in the returned result |
| --- | --- | --- |
| `first` | A and B | A and B |
| `second` | C | B and C |
| `third` | Handler skipped | B and C |

The table describes sets, without promising row order or invocation counts. Each run receives a distinct result reference.

With `cache="full"` and an initially empty store, the second call computes the complete B/C selection; the third reuses it. With `cache="off"`, each invoked computation receives its complete bound selection.

A partial comparison may need only C’s records and related area, but independent reference inputs remain complete. If the references select X/Y, both reference locations and their areas remain available. Changing those references to X/Z changes the comparison keys.

For an upstream producer with an original A/B/C selection, the policy determines how much work it can skip:

| Upstream policy | Work for a consumer that needs only C |
| --- | --- |
| `off` | Compute A/B/C |
| `full` | Reuse the complete A/B/C computation, or compute A/B/C on a miss |
| `per_location` | Reuse C, or compute C on a miss |

The consumer receives C’s related groups under every policy. Independent references and unpartitioned dependencies still supply their complete context.

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

Recreate runners and restart deployed processes after changing definitions. Processes sharing a store must honor the same immutable identities. [Provider revision coverage](../../plugins/location-providers/#define-revision-coverage) explains these responsibilities.

## Retain stored results

Local runs automatically use `.lyra/results.sqlite3`; set `result_store_path` to choose another file. The API and workers use one [shared SQLite path](../../operate/caching/) on one host.

Every successful managed execution receives a distinct final-result reference, including development runs, empty results, and cache hits. Several references can select the same stored values without copying rows. Lyra retains required final outputs and their retrieval descriptions; it writes intermediates when caching or ordinary retrieval needs them.

Lyra retains everything it writes indefinitely. Job expiration, changed keys, and cache-policy changes don’t delete stored data. Keep the local store path and result reference together to [reopen a result](../../use/results/#reopen-a-local-result). Remote methods accept job IDs, whose availability follows the server’s job retention setting.
