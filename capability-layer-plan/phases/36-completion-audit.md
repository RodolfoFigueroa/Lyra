# Phase 36 — Completion audit

Read the [shared workflow](index.md). Continue after
[phase 35](35-authoring-guidance.md). Review all **28 accepted ledger
decisions**, the deferred list, scope boundaries, and performance guidance.

Confirm the completed feature satisfies the agreed behavior across local, REST,
and MCP execution, with no unresolved integration gaps.

## Work

- Trace each accepted decision to implemented behavior and meaningful validation.
  Use code and tests as evidence; completed phase documents alone are not proof.
- Review boundaries most likely to fail across components: concrete source versus
  geographic identity; shared family schemas; explicit edition selection; route
  ties and preferences; cyclic conversions versus acyclic execution; preparation
  without acquisition; and effective-input binding ownership.
- Audit revision closure, target revisions, selection-only invalidation boundaries,
  native cache equivalence, run-wide revisions, immutable metadata reuse, and
  current-run provenance on cache hits.
- Exercise accepted remote bindings across catalog/revision changes, public/private
  filtering, empty conversions, missing IDs, failed-job inspection, bounded results,
  and shallow graph relationships.
- Resolve feature-related defects and remove abandoned scaffolding or superseded
  paths. Do not alter unrelated code merely to eliminate pre-existing findings.
- Check that examples and author guidance describe supported behavior and that
  deferred capabilities have not become accidental public requirements.

## Acceptance and validation

- Add focused isolated integration coverage only where a real behavioral gap
  remains. Use fake services, in-process transports, and temporary artifacts.
- Run formatting, linting, and type checking from the index, then the full suite
  with sandbox escalation and coverage generation:

  ```sh
  uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
  ```

- Run the permanent benchmark suite's useful final coverage, comparing with the
  baseline and previous comparable checkpoints. Repeat material differences;
  assess absolute costs, ratios, memory, and workload context. Report changed
  workloads, unavailable comparisons, and significant tradeoffs without inventing
  a universal regression threshold.
- Confirm ordinary benchmark workflows remain usable for subsequent development.
  Do not perform wheel or package-distribution checks.

## Completion

Report the implemented outcome, checks and findings, performance conclusions, and
any remaining limitations. An unimplemented accepted requirement remains unfinished
work; it must not be relabeled as deferred merely to close the audit.
