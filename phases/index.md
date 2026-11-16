# Milestone 12 implementation phases

Implement [retained location inputs](../feature-milestones/12-retained-location-inputs.md)
in the order below. Milestones 1–11 are the starting point. Each phase is intended
for a fresh session with the repository state left by its predecessors.

The milestone defines the public contract; these documents divide its
implementation. Read the milestone, this overview, and the selected phase before
editing. Read prerequisite handoffs and inspect the current code: paths below are
starting points, and earlier phases may have changed internal boundaries.

## Sequence

| Phase | Deliverable |
| --- | --- |
| [1. Descriptions and errors](01-descriptions-and-errors.md) | Public models, shared errors, and complete description-only access. |
| [2. Selection storage and local reads](02-selection-storage-and-local-reads.md) | Relational reconstruction references and native reads from retained fixtures. |
| [3. Fresh direct inputs](03-fresh-direct-inputs.md) | Capture and publish direct inputs with retained metric computations. |
| [4. Operations and routes](04-operations-and-routes.md) | Capture immediate correspondences and target records for derived inputs. |
| [5. Complete cache reuse](05-complete-cache-reuse.md) | Restore complete bindings from reused computations, including intermediate metrics used as roots. |
| [6. Partial reuse and group assembly](06-partial-reuse-and-group-assembly.md) | Combine fresh and retained partitions without losing shared context or leaking groups. |
| [7. Availability and skipped work](07-availability-and-skipped-work.md) | Finalize empty, unavailable, and matching shared-binding behavior across execution entry points. |
| [8. SQL pages and Arrow streams](08-sql-pages-and-arrow-streams.md) | Bounded input selection, interface-specific cursors, and generated Arrow streams. |
| [9. HTTP and MCP](09-http-and-mcp.md) | Authenticated input endpoints, tools, description fallback, and structured errors. |
| [10. Python clients](10-python-clients.md) | Synchronous and asynchronous native reads, pages, and downloads. |
| [11. Benchmarks and documentation](11-benchmarks-and-documentation.md) | Performance evidence, completed documentation, and closure of implementation handoffs. |
| [12. Read-only verification](12-read-only-verification.md) | Independent requirement audit and verification, with no implementation fixes. |

## Implementation approach

Extend the existing relational store, executor, and retrieval machinery in place.
Provider records and operation pairs already have separate storage; native
bindings already describe related selections; milestone 11 already supplies
description inspection, row encoding, Arrow conversion, and download lifecycles.
Use those boundaries instead of building a second implementation for a later
switchover. Introduce internal abstractions only where the next phase needs them.

The repository need not remain fully functional between phases. This permits a
small unfinished change with a precise handoff, not temporary compatibility
systems, duplicated execution paths, disabled caching, or fabricated availability.
Do not use `not_materialized` to conceal an unimplemented capture or reuse path.
It describes actual skipped execution without a complete retained binding.

Each implementation phase owns the tests, failure handling, mutation isolation,
and relevant documentation for its changes. Required input persistence belongs
with the write path that introduces it. Phase 11 must not become a deferred
integration or replacement phase, and phase 12 must not make fixes.

## Invariants throughout implementation

- Public lookup addresses are `(result_ref, input_name)`, for every named root
  `LocationDependency`. Ordinary metric dependencies are excluded. Metric output
  tables remain in `RunResult.tables`; location tables load only on request.
- Available inputs describe complete logical selections, not invocation batches.
  Direct records are unique within their provider scope; correspondences preserve
  immediate source/target pairs and target attributes. Input tables have no metric
  `row_key` or invented ownership.
- Retain captured schemas, nullability, units, CRS, code revisions, and origin
  descriptions. Identity comes from runtime bindings and existing qualified keys,
  never frame attributes or bare ID equality across providers.
- Store provider values once per existing identity, pairs separately, and shared
  selection descriptions by reference. Do not persist assembled forwarding tables
  or generated Arrow files. Retained computations carry reconstruction references
  independently of historical reports.
- A required persistence failure prevents successful publication. A damaged
  available input is an integrity failure, not an empty input, unavailable input,
  or reason to rerun work. Later executions cannot revise published availability.
- Inspection does not invoke handlers, construct selectors, query providers, or
  acquire external data. Description reads load no input/output tables or full
  reports. Returned frames and nested descriptions are detached from retained data.
- Retention does not authorize execution reuse. Preserve existing computation
  keys, declared cache policies, reduced execution, and honest report outcomes.

## Scope boundaries

Implement milestone 12 only. [Milestone 13](../feature-milestones/13-geoparquet-exports.md)
owns GeoParquet export. [Milestone 14](../feature-milestones/14-interventions.md)
owns effective datasets and intervention validation. Preserve source and target
scope roles so those later changes can extend them, but do not add speculative
intervention fields, request handling, or validation now.

Historical-store compatibility, cleanup policies, retention switches, fresh
provider browsing, internal graph navigation, invocation snapshots, and
finding-level contributing-row histories remain excluded. No new runtime
dependency is expected; follow the repository's approval rule if one is necessary.

## Verification for implementation phases

Follow [AGENTS.md](../AGENTS.md) and the session's repository instructions. After
changing Python, run:

```sh
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest -n auto <relevant test paths>
```

The test paths in each phase identify useful existing coverage. Add or extend
behavioral tests for the new requirements and run those too. Fix failures caused
by the change; report unrelated failures without editing unrelated code. Do not
add suppressions, skip tests, weaken assertions, or reduce coverage to pass checks.
Retain useful assertions when updating mixed tests.

Whenever running the full suite, use sandbox escalation
(`sandbox_permissions="require_escalated"`) and generate coverage:

```sh
uv run pytest -n auto --cov=lyra --cov-report=term-missing --cov-report=xml
```

Focused tests may run in the sandbox, but escalate if asyncio's internal socket
wakeups fail. Do not change application behavior to work around that restriction.
Use mocks and in-process fakes for all service and database acquisition, including
SQLite test connections; keep the service-access guard enabled. Temporary files
and in-process ASGI transports are allowed. Extend the existing SQL fake where
needed without replacing behavioral assertions with incidental helper counts.

Use the [benchmark guide](../benchmarks/USAGE.md) for performance work. Its
standalone persistence benchmarks use disposable production SQLite storage;
pytest still uses mocked connections. Use the existing environment, no live
external services or credentials, and no wheel or package-installation checks.
Do not write tests that pin documentation prose.

Report commands and outcomes, files analyzed, findings fixed, and any outstanding
checks. If a tool cannot run, include the attempted command, useful error output,
and the remaining verification obligation. Phase 12 uses check-only formatting
and the stricter read-only rules in its document.

## Handoffs between sessions

Before ending an implementation phase, update its handoff section with concrete
interfaces and decisions needed by successors. If behavior or a failing test is
deferred, also update the receiving phase document. A handoff must state:

- The exact behavior or test affected, with file or test identifiers.
- What currently works, what fails, and the known cause.
- The established internal interface or storage invariant the next phase must use.
- The specific remaining change and its owning phase.
- Commands already run and checks still outstanding.

Keep these entries concise and actionable; omit timestamps, commit inventories,
session transcripts, and repeated plans. Deferral does not mean that milestone
behavior is complete. An unrecorded gap must not silently become phase 11's work.
Resolve implementation handoffs before phase 12; the verifier reports newly found
gaps in its response without changing these documents.
