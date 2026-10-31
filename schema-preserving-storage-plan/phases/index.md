# Schema-preserving storage implementation

Implement these phases in order, one fresh session per phase. The
[decision ledger](../decision_ledger.md) defines the intended behavior. These
documents divide the work; they do not replace that contract.

## Phases

| Phase | Outcome |
| --- | --- |
| [01 — Acceptance fixtures and baseline](01-acceptance-fixtures-and-baseline.md) | Reusable declared fixtures and measurements of the current integrated paths. |
| [02 — Schema and frame normalization](02-schema-and-frame-normalization.md) | Exact columns, declaration order, and index-independent validation. |
| [03 — Shared Arrow conversion](03-shared-arrow-conversion.md) | Declaration-derived types and faithful native frame round trips. |
| [04 — Immutable typed storage](04-immutable-typed-storage.md) | Owned internal snapshots retaining types and existing geometry semantics. |
| [05 — Typed content fingerprints](05-typed-content-fingerprints.md) | Versioned exact identity independent of physical Arrow layout. |
| [06 — Unified IPC codec](06-unified-ipc-codec.md) | Uncompressed typed artifacts with structural and integrity checks. |
| [07 — Schema-required location construction](07-schema-required-location-construction.md) | Explicit construction contracts and updated callers and fixtures. |
| [08 — Typed location runtime](08-typed-location-runtime.md) | Typed properties, lazy access, selections, and native frame exports. |
| [09 — Presentation exports and inspection](09-presentation-exports-and-inspection.md) | Explicit serialization and bounded inspection of typed contents. |
| [10 — Location validation and reuse](10-location-validation-and-reuse.md) | Full current-schema checks without rebuilding unchanged snapshots. |
| [11 — Metric cache integration](11-metric-cache-integration.md) | IPC metric bundles with native execution and restored-result validation. |
| [12 — Location artifact and cache integration](12-location-artifact-and-cache-integration.md) | Verified standalone and cached location paths using the unified protocol. |
| [13 — Application and authoring integration](13-application-and-authoring-integration.md) | Consistent local, worker, provider, example, and authoring behavior. |
| [14 — Integrated acceptance and performance](14-integrated-acceptance-and-performance.md) | Complete acceptance coverage and measured integrated costs. |

## Working on a phase

Read the repository instructions, this index, the ledger, and the requested phase.
Inspect the current implementation and relevant tests: earlier sessions may have
chosen different module boundaries than the original code. Choose files and
internal interfaces to suit the implementation; the documents prescribe behavior,
not a file inventory.

Complete earlier phases before starting a later one. Do not redo completed work
or implement the entire feature in one session. Foundation phases may introduce
private, tested components before connecting them to production callers. This
staging does not require compatibility readers, public aliases, or configurable
old/new backends.

Each phase must leave its affected paths working and its applicable checks
passing. Update direct callers, tests, and affected authoring guidance with the
change. If switching a representation also changes a reader or export, connect
that caller in the same phase. Later integration phases add broader coverage and
remove duplication; they do not excuse broken intermediate behavior. In
particular, phase 08 must keep location artifacts and exports working using the
foundations from phases 03–06.

Closed-alpha changes can replace existing contracts directly. Remove obsolete
behavior and its author-facing examples. Do not add migrations, shims, old-format
readers, or migration prose. Keep READMEs short. Preserve native metric frames,
the existing geometry contract, and the existing computation-key policy. The
ledger's exclusions apply throughout, including custom dtype frameworks,
categorical repair codecs, consumer schemas, and executor concurrency changes.

## Verification for every implementation session

Use `uv` and the existing project environment. Prefer existing dependencies;
obtain approval before adding a runtime dependency. After changing Python, run:

```sh
uv run ruff format
uv run ruff check
uv run ty check
```

Run focused tests for the changed behavior and its immediate callers. The phase
documents identify acceptance cases, not a mandatory test-file layout. Add tests
as behavior is implemented, without failing or skipped placeholders for future
phases. Preserve meaningful assertions when replacing obsolete APIs.

Run the full suite when the phase requests it or a change affects broad runtime
boundaries. Use sandbox escalation (`sandbox_permissions="require_escalated"`)
and generate `coverage.xml`:

```sh
uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
```

Escalate focused tests too if asyncio's internal socket wakeups are blocked.
Keep the service-access guard enabled. Use mocks or in-process fakes for all
external resources, including localhost services; temporary artifact files and
in-process ASGI tests are permitted. Do not perform wheel or installation smoke
checks, weaken verification rules, or add tests that pin documentation wording.

Fix failures caused by the phase and rerun the affected checks. Report unrelated
failures and unavailable tools with the attempted command and useful error
output. Finish with a short handoff describing implemented behavior, actual code
entry points, verification outcomes, and limitations relevant to the next phase.
No separate status logs or session metadata are needed.
