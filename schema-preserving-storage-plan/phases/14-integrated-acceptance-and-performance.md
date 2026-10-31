# Phase 14 — Integrated acceptance and performance

After phase 13, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Verify the completed feature and measure
its integrated costs against phase 01.

## Work

- Review existing coverage against every ledger acceptance requirement. Add only
  missing behavioral coverage for fresh results, subsets, artifact reads,
  cache-restored results, and downstream consumers.
- Exercise the combined fidelity matrix: empty/all-null columns, nullable native
  types, numeric widths and boundaries, signed zero, temporal precision/timezone,
  date-only values, normalized indexes, column/row order, and spatial metadata.
- Confirm mutation isolation, exact fingerprint sensitivity, chunk/slice layout
  independence, lazy inspection, selective decoding, bounded exports, and
  single-pass geometry ingestion. Give categories basic native coverage and
  retain the known empty-category limitation explicitly.
- Repeat integrated measurements with the phase 01 workloads, using equivalent
  valid typed contents. Measure fresh execution and cache-hit paths, distinguishing
  validation, conversion/ownership, hashing, and storage as far as the harness
  supports. Include artifact size and useful memory observations.
- Explain material regressions or unexpected repeated work. Make bounded fixes
  justified by evidence and rerun affected checks; do not reopen the selected
  format, introduce custom category repair, or redesign executor concurrency.

## Acceptance

- The ledger's supported behavior is covered by passing isolated tests, including
  current-schema validation after successful artifact integrity checks.
- There are no unfinished implementation placeholders or compatibility paths.
  Known native categorical limitations are reported separately from required
  fidelity guarantees and are not hidden by weakened assertions.
- Performance comparisons state what was measured, which costs are combined,
  fixture validity, and measurement variability. A current path that could not
  preserve a fixture is not presented as an equivalent faster baseline.
- No end-to-end speedup is claimed from codec-only measurements. Deterministic
  tests guard avoidable repeated work; benchmark timings remain observations
  rather than flaky unit-test thresholds.

## Verification and boundary

Run shared formatting, linting, and type checks, any added focused tests, and the
full suite with the required escalation and XML coverage. Run timing campaigns
separately from competing checks. Preserve a concise acceptance/performance
report with useful measurements and limitations outside READMEs. Report any
unresolved requirement or verification failure honestly; do not mark the feature
complete solely because its final phase was attempted.
