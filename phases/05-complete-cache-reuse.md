# Phase 5: Complete cache reuse

## Objective and prerequisites

Make a completely reused metric result expose its retained location inputs without
fresh materialization. Complete [phase 4](04-operations-and-routes.md) and read the
capture/storage handoffs from phases 2–4. Follow the [overview](index.md),
[milestone 9](../feature-milestones/09-node-caching.md), and
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

Fresh direct and derived computations should already persist reconstruction
references. This phase consumes those references for full-cache and complete
per-location hits. Mixed fresh/cached assembly belongs to phase 6.

## Implementation

Extend metric lookup and result publication to obtain input bindings from retained
metric computations. Use existing computation keys and qualified scopes. Restoring
these bindings must not require input geometry, current provider acquisition,
historical reports, or original invocation records. Keep ordinary execution
identity and scheduling intact.

Support both a `full` computation hit and requests whose required `per_location`
computations are all hits. Assemble their reference descriptions sufficiently to
describe complete direct and correspondence inputs, with shared context retained
once. The following phase expands this to mixed hits and more involved group
restriction; do not introduce a separate full-hit storage format now.

Cover a metric originally retained as an intermediate dependency and subsequently
requested as the root. Its named inputs must be available through its own retained
bindings, even if the producing root failed later or its report is absent. The
new result receives its own description and current report; it does not follow a
producer's report to recover input membership.

Validate the required reconstruction relationships as part of accepting retained
computations. A valid completed metric with damaged required input references is
an integrity failure. Do not turn it into a cache miss, silently downgrade
availability, or acquire records to repair it. Inspection of missing values must
likewise fail. Preserve independently valid values after a later publication fails.

Retaining records while provider caching is disabled must not change acquisition
for fresh work. A downstream metric hit can still bypass acquisition entirely.
Similarly, skip selector construction, predicate query execution, and mapping
handlers when ordinary reuse already makes their work unnecessary. Do not promise
that this phase eliminates upstream work required by the existing identity rules;
the requirement is no extra work to populate inspection.

Keep reports honest: available inputs do not imply fresh acquisition or handler
execution. Test reuse across local, development, and worker entry points and across
fresh executor instances. Declared provider/code/origin changes must continue to
invalidate identities under existing rules, while old results remain readable
from their captured descriptions.

## Starting points

- [Metric lookup and execution](../src/lyra/runtime/execution.py), including both
  bound and resolved invocation paths and in-execution memory reuse.
- [Metric tables](../src/lyra/runtime/metric_tables.py),
  [reuse policy](../src/lyra/runtime/reuse.py), and
  [result publication](../src/lyra/runtime/result_descriptions.py).
- [Binding identities](../src/lyra/runtime/planning/bindings.py) and the internal
  input-reference APIs established in phases 2–4.

## Verification and completion

Exercise complete hits for direct inputs, shared references, explicit operations,
routes, zero-match correspondences, unpartitioned metrics, and empty metric
outputs with nonempty inputs. Include intermediate-as-root reuse after both a
successful producing run and a later downstream failure.

Make acquisition and handler/selector execution fail if invoked unnecessarily.
For cache-hit publication, forbid loading input geometry and decoding historical
reports. Corrupt references and retained values separately and check integrity
behavior without recomputation. Verify no extra work appears in execution reports.

Run the overview's Python checks and relevant tests:

```sh
uv run pytest -n auto tests/test_run_reuse.py tests/test_complete_operation_reuse.py tests/test_operation_chain_reuse.py tests/test_retention_outcomes.py tests/test_entry_point_cache_parity.py
```

Completion means complete hits retain the same inspectable logical inputs as
their originating computations. Partially reused requests may still have explicit
phase-6 gaps, but no such gap may be represented as legitimate unavailability.

## Handoff

Pass the binding lookup, validation, and complete-hit assembly interfaces to
[phase 6](06-partial-reuse-and-group-assembly.md). Record exact mixed-hit and group
selection failures there. Record only genuine skipped-binding availability work
in [phase 7](07-availability-and-skipped-work.md). Include any outstanding
geometry-free publication or historical-report-independence checks.
