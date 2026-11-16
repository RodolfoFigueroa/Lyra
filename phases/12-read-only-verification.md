# Phase 12: Read-only verification

## Objective and prerequisites

Independently verify the completed retained-location-input feature against
[milestone 12](../feature-milestones/12-retained-location-inputs.md). Complete
[phase 11](11-benchmarks-and-documentation.md), read its verification handoff and
the [overview](index.md), and inspect earlier phase handoffs for unresolved work.

This phase makes no implementation, test, benchmark, configuration, dependency,
or documentation edits. Do not apply fixes, run auto-fix/formatting commands, add
tests, or update phase documents. Running checks may create normal artifacts such
as `coverage.xml`, test caches, disposable benchmark data, or temporary build
output; that does not authorize edits to project sources. Inspect the working
tree before and after verification and preserve pre-existing user changes.

## Requirement audit

Read the milestone itself, not just this checklist. For each requirement, inspect
the implementation and identify behavioral test evidence. Passing existing tests
does not establish a requirement those tests do not exercise.

| Area | Required evidence | Owning implementation phases |
| --- | --- | --- |
| Public descriptions | Every root location dependency; empty mapping for no-input roots; schemas, scopes, availability, strict validation, detached nested fields. | 1–4, 7 |
| Metadata-only access | Local and remote complete descriptions without input/output table loading or complete report decoding; eager output/report behavior of `read_result()` preserved. | 1–2, 9–10 |
| Direct native reads | Unique qualified provider records, logical dtypes/nullability, active geometry/CRS, no metric `row_key`, usable after closure. | 2–3 |
| Derived native reads | Explicit/automatic/receiving routes, immediate scopes, repeated targets, multi-hop final pairs, target attributes, no unrelated groups. | 2, 4, 6 |
| Computation retention | Reconstruction references with every retained metric computation, including intermediates; no retention of every intermediate output solely for history. | 2–5 |
| Full and mixed reuse | A/B cached plus fresh C yields A/B/C; complete shared context once; appropriate groups from full producers; intermediate later used as root. | 5–6 |
| Availability | Known empty and completed zero matches differ from skipped unresolved inputs; empty output does not imply empty input. | 3–4, 7 |
| Binding evidence | Unrelated cached records do not upgrade skipped inputs; a complete matching same-execution binding can qualify; later runs do not change published availability. | 7 |
| Identity and mutation | Handler/caller mutations cannot change values or descriptions; provider/code/origin changes and equal bare IDs preserve qualified distinctions. | 2–7 |
| Cache independence | Disabled provider caching still acquires for fresh work; downstream hits bypass unnecessary acquisition; no extra selectors/queries/handlers for inspection. | 3–7 |
| Durable success and integrity | Required failures prevent local/remote success; missing available data raises integrity errors without downgrade, cache invalidation, or recomputation. | 2–7, 9–10 |
| Retained independence | Reopening, job expiry, changed registrations, and absent historical producer reports preserve ordinary retrieval/reuse. | 2, 5, 7, 9 |
| Storage costs | Provider records and pairs separate; shared membership references; no stored joined geometries, assembled input copies, or permanent Arrow artifacts. | 2–6, 11 |
| Rows and cursors | Bounded SQL-selected pages, complete-row/encoded limits, JSON encodings, cross-interface rejection, exactly-once traversal, oversized-row fallback. | 8–10 |
| Arrow and lifecycle | Native/Arrow agreement, empty schemas, GeoArrow WKB/CRS, bounded streaming, cleanup on failure/disconnect/cancellation. | 8–10 |
| HTTP/MCP | Both new endpoints/tools, authentication, public-base absolute URLs, 200/Arrow media type, SDK envelopes and separate argument validation. | 9 |
| Description limits | Whole-description preview for many inputs or large schemas; full download preserves everything; Python clients automatically retrieve it. | 9–10 |
| Errors | Unavailable 409 and all existing categories; input/output context, pagination parameter, download URL, bounded payloads, code-based client mapping. | 1, 8–10 |
| Clients | Sync/async parity, independent native frames, pages, `str`/`Path` downloads returning `None`, replacement/failure/cancellation/filesystem behavior. | 10 |
| Performance and guidance | Representative calibrated measurements, retention-growth evidence, current documentation/examples, no timing gates or claims of bounded retention. | 11 |

Confirm the same supported behavior across local, development, and remote worker
execution. Inspect both bound and native execution paths. In cache tests,
distinguish prohibited inspection work from work already required by ordinary
identity resolution; reports must describe what actually happened.

Confirm scope exclusions: no GeoParquet work, intervention execution/validation,
historical-store compatibility, public group/row trace identities, cleanup
controls, provider browsing, or arbitrary metric-dependency retrieval was added
under this milestone. Future intervention passages in the milestone are extension
requirements for milestone 14, not missing features to implement during this audit.

## Run verification

Use the existing project environment without installing packages, rebuilding
wheels, changing dependency files, or creating temporary installation environments.
Run check-only formatting, linting, and typing:

```sh
uv run ruff format --check
uv run ruff check
uv run ty check
```

Run the full suite with sandbox escalation
(`sandbox_permissions="require_escalated"`) and coverage:

```sh
uv run pytest -n auto --cov=lyra --cov-report=term-missing --cov-report=xml
```

Keep service/database acquisition mocked and the service-access guard enabled.
Do not start services or use live credentials. If a focused diagnostic test needs
asyncio wakeups unavailable in the sandbox, escalate it under the repository rule.
Do not weaken tests or modify application code to work around the environment.

Inspect phase 11's benchmark evidence and run appropriate existing-environment
checks from the [benchmark guide](../benchmarks/USAGE.md). Use focused selectors
recorded in the handoff. A `--quick --dry-run` run checks benchmark execution only;
it does not replace normal calibrated measurements. Keep benchmark fixtures in
disposable locations and use no external services.

Check affected documentation links, structure, and executable examples with the
existing tooling. Inspect commands before running them: documentation generation
that rewrites tracked source files is outside this phase. Use existing generated
artifacts or disposable output where supported; otherwise report that check as
outstanding instead of editing or regenerating project sources.

Do not rerun broad checks without a diagnostic reason. If a required command is
unavailable or fails, record its invocation, useful output, and the unverified
requirements. Separate environmental restrictions and evidenced pre-existing
failures from failures introduced by this milestone.

## Verification report

Return findings to the user without editing the repository to record them.
Lead with any correctness, durability, or resource-lifecycle failures. For each
finding, give the affected requirement, file/test evidence, reproduction command
when available, practical consequence, and owning implementation phase.

Report commands and outcomes, coverage generation, benchmark evidence and limits,
unresolved handoffs, and checks that could not run. Distinguish observed failures
from requirements lacking adequate evidence. Confirm whether any project source
changed during verification and explain unexpected changes without overwriting
user work.

Declare milestone 12 verified only when the full required contract has supporting
evidence and no unresolved implementation gap remains. Otherwise report the
specific gaps; leave their fixes to a subsequent implementation session and keep
this phase read-only.
