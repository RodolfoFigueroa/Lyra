# Phase 7: Availability and skipped work

## Objective and prerequisites

Finish availability classification and the local execution contract, including
bindings whose producing work was skipped. Complete
[phase 6](06-partial-reuse-and-group-assembly.md), read all runtime handoffs from
phases 3–6, and follow the [overview](index.md). Read “Availability and empty inputs”
and the completion boundary in
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

Fresh capture, retained computation reuse, and mixed assembly are already in place.
This phase resolves actual missing-materialization cases; it must not conceal
unfinished capture or assembly by labelling those inputs unavailable.

## Implementation

Finalize a description for every named root location input, even when the root
handler or an upstream operation never runs. Determine availability from complete
bindings captured for this result or its reused metric computations. Use retained
schemas and resolved declarations for descriptions without acquiring values.

Apply these distinctions:

| Situation | Required result |
| --- | --- |
| Known empty direct selection | `available`, with a valid empty GeoDataFrame. |
| Retained completed zero-match correspondence | `available`, with the forwarding schema and both scopes. |
| Nonempty input supplied to a metric producing no rows | `available`, with the nonempty input. |
| Zero-primary request skips a nonempty reference with no complete binding | Primary is known empty; reference is `not_materialized`. |
| Derived selection never resolved | Do not infer emptiness from skipped execution. |
| Complete matching binding already resolved elsewhere in this execution | May be retained for this result without extra work. |
| Records or mappings from an unrelated earlier execution | Do not establish this result's selected binding. |
| Required data missing from an available input | Integrity failure; preserve its published availability. |

For same-execution reuse, match the complete logical selection, provider/source
scopes, and applicable selected groups. Mere overlap or presence in a resolver's
record pool is insufficient. Retain the qualifying references with this result;
do not leave availability dependent on transient execution state.

Do not acquire a reference input or run an upstream operation solely to make it
inspectable. Avoid scanning unrelated cached records or mappings to upgrade a
skipped input. A result's published availability is immutable: subsequent runs
may retain new values but must not opportunistically change an old result.

`read_location_input()` raises `LocationInputUnavailableError` for unavailable
inputs, never an empty or partial table. Available input corruption remains an
integrity failure. Availability describes retrievable values, not work observed
in the current report. Fully cached available inputs keep honest hit/skipped
outcomes; known-empty primary work stays skipped where existing execution rules
require it.

Close runtime entry-point gaps for local, development, and worker execution.
Required provider, pair, schema, selection-reference, or final-result persistence
failure must prevent successful result publication and use the existing local
persistence error/remote failed-job behavior. Verify this across the completed
direct, derived, full-hit, and mixed-hit paths rather than adding a separate
success-finalization mechanism now.

Milestone 14 intervention validation is not implemented here. In particular, do
not add speculative dataset acquisition to establish future availability rules.

## Starting points

- [Bound resolution and root publication](../src/lyra/runtime/execution.py),
  [binding membership](../src/lyra/runtime/planning/bindings.py), and
  [resolver state](../src/lyra/runtime/locations.py).
- [Descriptions](../src/lyra/runtime/result_descriptions.py),
  [report collection](../src/lyra/runtime/report_collection.py), and
  [worker completion](../src/lyra/server/worker.py).

## Verification and completion

Test each row of the availability table with isolated acquisition boundaries.
Include wrong-scope and incomplete same-execution bindings that must not qualify,
and an exact matching binding that does qualify. Publish a skipped result, then
perform another run supplying the missing records; the original remains unavailable.
For empty frames, assert declared dtypes, active geometry, and CRS explicitly.

Test description and value reads after store reopening, job expiry, and changed
registrations, plus returned-object mutation isolation. Verify descriptions do
not load tables or reports, including for unavailable inputs. Exercise persistence
faults and honest execution outcomes across the supported entry points.

Run the overview's Python checks, new availability tests, and relevant coverage:

```sh
uv run pytest -n auto tests/test_partial_metric_reuse.py tests/test_retention_outcomes.py tests/test_local_reports.py tests/test_worker_reports.py tests/test_result_descriptions.py tests/test_entry_point_cache_parity.py
```

Completion means the entire local milestone-12 retention/read contract works.
Resolve outstanding runtime handoffs from phases 3–6 before proceeding; SQL
pagination and public transport/client access are the remaining feature work.

## Handoff

Give [phase 8](08-sql-pages-and-arrow-streams.md) the completed lookup, selection,
availability, and integrity APIs. Give [phase 9](09-http-and-mcp.md) representative
empty/unavailable/error fixtures. Record any genuine remaining failure in its
owning phase document; do not leave an unspecified runtime integration task for
phase 11 or the read-only verifier.
