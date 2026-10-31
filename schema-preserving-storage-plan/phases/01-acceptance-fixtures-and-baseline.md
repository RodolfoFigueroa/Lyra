# Phase 01 — Acceptance fixtures and performance baseline

Read the [index](index.md) and [decision ledger](../decision_ledger.md). This is
the first phase; it establishes evidence and fixtures without replacing storage.

## Work

- Create or consolidate small reusable native-frame fixtures with explicit
  Pandera declarations. Cover ordinary and spatial frames, named metric outputs,
  nullable columns, numeric widths, timestamps with precision and timezone,
  Arrow-backed dates, and a basic categorical column.
- Include populated, empty, and all-null variants where the declarations permit
  them. Include shuffled columns, incidental indexes, signed zero, and numeric
  boundary cases. Build expected values from the declared contract rather than
  inferring expected dtypes from whatever the implementation returns.
- Reuse the existing storage and location benchmarks where practical. Capture
  current fresh execution, cache write, cache read plus validation, location
  construction, and representative selection/export costs. Record repeatable
  workload definitions, timings, artifact sizes, and useful memory observations.
- Separate producer validation, conversion/ownership, fingerprinting, and storage
  where the current implementation permits measurement. Identify combined costs
  honestly instead of claiming they are isolated.
- Preserve a compact baseline report and reusable measurements in an appropriate
  planning or benchmark location. The existing format-comparison report remains
  evidence about codecs, not a substitute for this integrated baseline.

## Acceptance

- Fixture declarations validate their intended valid frames; invalid variants are
  clearly distinguished. Nullable fixtures use native dtypes that can represent
  their declared nulls.
- Baseline comparisons use equivalent valid typed contents. If the current SDK
  cannot preserve a fixture, record that limitation and exclude it from direct
  speed comparisons rather than silently simplifying its types or values.
- Measurements can be repeated after implementation without retaining an old
  runtime backend. Record observations without adding flaky timing thresholds.
- Fixture and harness tests pass now. Do not introduce tests that intentionally
  fail until later phases or weaken existing fidelity assertions.

## Verification and boundary

Follow the shared verification instructions and run the relevant fixture and
benchmark-harness tests. Run the baseline campaign separately from competing
tests. No external services are needed. Leave production storage behavior
unchanged; later phases use these fixtures to add their own acceptance tests.
