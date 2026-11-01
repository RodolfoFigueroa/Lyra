# Phase 01 — Performance baseline

Read the [shared workflow](index.md) and the ledger's
[performance guidance](../decision-ledger.md#performance-validation-during-implementation).
This phase precedes feature implementation.

Capture reproducible measurements of the existing application so later sessions
can assess cumulative costs without maintaining an old implementation.

## Work

- Inspect the permanent benchmark suite and its
  [usage guide](../../benchmarks/USAGE.md). Identify the current catalog,
  preparation, execution, caching, data, and interface workloads.
- Measure existing behavior from a clean commit using the existing project
  environment. Save raw samples and environment information through the documented
  ASV workflow. Repeat useful measurements when practical to understand variability.
- Use the full profile if practical; explicitly identify meaningful gaps if only
  a representative subset can run. Keep benchmark setup and measured operations
  distinct when interpreting costs.
- Ensure the retained baseline remains available to subsequent sessions through
  the suite's normal storage. Keep any explanation of gaps or workload choices
  concise; do not copy machine metadata or commit identifiers into phase documents.

## Acceptance and validation

- Saved results identify the existing workloads and preserve raw samples and the
  environment information needed for comparison.
- The suite runs with in-process providers and service substitutes, without live
  services or credentials.
- Confirm that saved results can be located and compared using the existing
  workflow. A quick benchmark smoke run alone is not a performance baseline.
- Report benchmark failures or environmental limitations separately from observed
  application costs. If repairing benchmark plumbing is necessary, keep it narrow
  and run the Python checks from the index for those changes.

## Boundary

Do not implement capability-layer behavior or redesign the benchmark suite. Do not
build packages, install wheels, introduce timing gates, or modify unrelated user
changes to obtain a clean measurement. Follow the benchmark guide for handling
uncommitted work.
