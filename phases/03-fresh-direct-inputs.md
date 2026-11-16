# Phase 3: Fresh direct inputs

## Objective and prerequisites

Capture and durably retain direct-provider location inputs from fresh metric
execution. Complete [phase 2](02-selection-storage-and-local-reads.md), read its
handoff, and follow the [overview](index.md) and
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

The storage and native reader should already work with retained fixtures. This
phase connects that implementation to both bound scheduling and native/resolved
invocations. Do not create a second capture system for either execution path.

## Implementation

Capture complete direct bindings for every named `LocationDependency` supplied
to a freshly evaluated metric: primary inputs and independent reference inputs.
Use validated execution-owned records and resolved descriptions before a handler
receives mutable working frames. Read original bindings when a handler changes
IDs, geometry, CRS, attributes, or frame metadata.

Integrate reference publication wherever metric computations are already retained,
including intermediate metrics retained for caching. Attach input references to
their existing computation keys even when the metric is not the current root.
Do not retain every intermediate output merely to create a history. For
per-location publication, associate related selections with the appropriate
partition and refer to complete shared context without duplicating its membership.

Ensure required provider records are persisted even when provider caching is
disabled. Retaining them does not authorize record-cache hits: a fresh computation
still acquires required records under the declared policy. Reuse within the same
execution keeps its existing semantics. Avoid retaining unrelated acquired
candidate records solely for this interface.

Use phase 2's completion boundary for metric outputs and input references.
Required persistence failure must surface through `ExecutionPersistenceError`
locally and failed jobs remotely; no final description/report pair may advertise
success with incomplete available inputs. Persistence and capture are part of
this phase, not a later reliability task.

Publish captured input descriptions in local result metadata and retained result
descriptions. Metric output tables stay unchanged in `RunResult.tables` and
`read_result()`. Establish direct-input reads after writer closure and reopening,
without loading plugins or acquiring values. Include metrics without a primary
input and roots without location inputs.

Known materialized empty direct inputs retain their schema and scope. A metric
that returns no output rows can still have nonempty retained inputs. Preserve
these distinctions now; phase 7 owns the broader skipped-root availability rules.
Do not infer selected input membership from output owners or output row counts.

## Starting points

- [Executor](../src/lyra/runtime/execution.py): `_bound_metric`,
  `_materialize_bound_invocation`, `_metric`, `_publish_metric`, and final-result
  publication, or their replacements from earlier phases.
- [Native inputs](../src/lyra/runtime/native_inputs.py),
  [bindings](../src/lyra/runtime/planning/bindings.py), and
  [invocation ownership](../src/lyra/runtime/invocation.py).
- [Location resolver](../src/lyra/runtime/locations.py),
  [provider acquisition](../src/lyra/runtime/provider_acquisition.py), and
  [persistence boundary](../src/lyra/runtime/persistence.py).
- [Local execution](../src/lyra/runtime/local.py) and
  [worker execution](../src/lyra/server/worker.py).

## Verification and completion

Use isolated providers and database fakes. Cover one primary input, multiple
direct references, a shared reference across partitions, unpartitioned metrics,
no-location roots, empty materialized inputs, and nonempty inputs producing empty
outputs. Test retention with provider caching both enabled and disabled.

Exercise both execution paths and local/development/worker entry points for the
fresh direct cases. Deliberately mutate handler arguments, returned frames, and
nested descriptions; later reads must preserve the captured values. Fail provider
record, binding, metric, and final-result persistence and verify publication rules.
Verify intermediate metric computations receive references even before phase 5
adds their reuse-as-root behavior.

Run the overview's Python checks and focused tests, including new input tests:

```sh
uv run pytest -n auto tests/test_execution_storage.py tests/test_retention_outcomes.py tests/test_cache_native_ownership.py tests/test_provider_reuse.py tests/test_run_result.py tests/test_entry_point_cache_parity.py
```

Completion means fresh direct metric inputs can be described and read through the
actual execution/store path. No full-cache or mixed-cache success claim is implied
until phases 5–6 consume the references.

## Handoff

Pass capture entry points, per-computation reference ownership, and transaction
behavior to [phase 4](04-operations-and-routes.md). Record cache-path gaps and
exact failures in [phase 5](05-complete-cache-reuse.md) and
[phase 6](06-partial-reuse-and-group-assembly.md). Send unresolved skipped-root
cases to [phase 7](07-availability-and-skipped-work.md). Do not hide those gaps by
disabling caching or substituting unavailable descriptions.
