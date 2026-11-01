# Capability layer implementation phases

Implement these phases in order, using a fresh session for each. The
[decision ledger](../decision-ledger.md) defines the required behavior; these
documents divide the work into bounded, testable increments. Phase numbers do not
correspond to ledger decision numbers.

## Phase overview

| Phase | Deliverable |
| --- | --- |
| [01 — Performance baseline](01-performance-baseline.md) | Retained measurements of existing behavior. |
| [02 — Location contract models](02-location-contract-models.md) | Shared schemas, concrete contracts, identity, and source metadata. |
| [03 — Location value identity](03-location-value-identity.md) | Concrete identity preserved in location values, fingerprints, and artifacts. |
| [04 — Provider contracts](04-provider-contracts.md) | Provider registration and lookup using concrete contracts. |
| [05 — Typed node declarations](05-typed-node-declarations.md) | Concrete contracts for named location inputs and operation outputs. |
| [06 — Concrete dependency references](06-concrete-dependency-references.md) | Producer/output references and typed dependency validation. |
| [07 — Edition registration](07-edition-registration.md) | Factory-built variants and explicit or implicit editions. |
| [08 — Family validation](08-family-validation.md) | One shared structural and ordinary-parameter contract per family. |
| [09 — Metric visibility](09-metric-visibility.md) | Public/private entry-point policy with private dependency execution. |
| [10 — Shared catalog compilation](10-shared-catalog-compilation.md) | One compiled core catalog for application and local use. |
| [11 — Canonical-operation validation](11-canonical-operation-validation.md) | Eligible typed routing edges with satisfiable dependencies. |
| [12 — Shortest-route computation](12-shortest-route-computation.md) | Reachability, shortest routes, cycles, and ambiguity detection. |
| [13 — Route preferences](13-route-preferences.md) | Validated operation sequences for route ties. |
| [14 — Accepted source selections](14-accepted-source-selections.md) | Unambiguous accepted source contracts per variant and input. |
| [15 — Revision planning](15-revision-planning.md) | Scoped revision closures and separate structural namespaces. |
| [16 — Shared request preparation](16-shared-request-preparation.md) | Validated, immutable execution bindings before acquisition. |
| [17 — Pinned location acquisition](17-pinned-location-acquisition.md) | Contract-checked ID resolution honoring pinned revisions. |
| [18 — Canonical route execution](18-canonical-route-execution.md) | Batched selectors running through the existing executor. |
| [19 — Dependency binding semantics](19-dependency-binding-semantics.md) | Correct ownership, forwarding, defaults, and plan acyclicity. |
| [20 — Revision-aware invocation caching](20-revision-aware-invocation-caching.md) | Concrete invocation keys and dependency-aware data invalidation. |
| [21 — Canonical selection cache reuse](21-canonical-selection-cache-reuse.md) | Selector caching and metric reuse across equivalent native inputs. |
| [22 — Immutable metadata reuse](22-immutable-metadata-reuse.md) | Reused namespaces, revision identities, and fingerprints. |
| [23 — Execution provenance](23-execution-provenance.md) | Accepted bindings and accurate observations, including cache reuse. |
| [24 — Public local execution](24-public-local-execution.md) | Canonical-only local calls, native returns, and separate inspection. |
| [25 — Bound-plan serialization](25-bound-plan-serialization.md) | Lossless, validated serialization of accepted execution bindings. |
| [26 — Worker bound-plan execution](26-worker-bound-plan-execution.md) | Worker execution of captured bindings without replanning. |
| [27 — Remote acceptance and persistence](27-remote-acceptance-and-persistence.md) | Shared preparation and durable bindings before enqueueing. |
| [28 — Discovery and inspection](28-discovery-and-inspection.md) | Compact families, edition-aware search, and detailed contracts. |
| [29 — REST contracts](29-rest-contracts.md) | Public HTTP discovery, submission, status, and result projections. |
| [30 — Python client workflows](30-python-client-workflows.md) | Consistent synchronous and asynchronous capability workflows. |
| [31 — MCP discovery and inspection](31-mcp-discovery-and-inspection.md) | Compact family tools and concrete-edition inspection. |
| [32 — MCP execution and results](32-mcp-execution-and-results.md) | Canonical submission, polling, bounded results, and failure semantics. |
| [33 — Shallow dependency inspection](33-shallow-dependency-inspection.md) | One bounded lookup of direct dependencies and consumers. |
| [34 — Authoring examples](34-authoring-examples.md) | Small executable examples covering the supported capability contract. |
| [35 — Authoring guidance](35-authoring-guidance.md) | Consistent current-contract documentation and bundled skill references. |
| [36 — Completion audit](36-completion-audit.md) | Ledger coverage, integrated verification, and cumulative performance review. |

## Working on one phase

Read the repository instructions, this index, the selected phase, and its referenced
ledger decisions. Inspect the preceding implementation and relevant tests; phase
documents describe intended outcomes, while current code establishes what exists.

Start with the relevant SDK declaration, graph, location, preparation, or executor
code for core work, and application/client code for interface work. File layouts,
names, model shapes, and API spellings remain implementation choices.

Leave each component usable and tested. Update affected callers, fixtures, examples,
and current-contract guidance when changing an interface, even if a later phase
expands those areas. Internal components may precede public integration; do not
expose incomplete behavior or defer fixes caused by the phase. Avoid speculative
scaffolding for later phases.

Earlier model and compiler phases own direct component validation and necessary
caller adjustments. Later integration phases own complete public workflows; do not
pull their full scope forward simply because an earlier phase changes a shared model.

Lyra is in closed alpha. Replace superseded interfaces directly, without migrations,
compatibility shims, or parallel legacy implementations. Keep deferred ledger
features out of scope. Describe supported behavior directly and keep READMEs short.

## Verification and handoff

For every phase that changes Python, run from the repository root:

```sh
uv run ruff format
uv run ruff check
uv run ty check
```

Run affected tests and new behavioral cases with `uv run pytest`. Fix failures
caused by the phase and rerun affected checks. Use `uv` for Python commands and
dependency management; obtain approval before adding a runtime dependency unless
separately authorized.

When running the full suite, use sandbox escalation
(`sandbox_permissions="require_escalated"`) and generate coverage:

```sh
uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
```

Escalate focused tests too if the sandbox blocks asyncio's internal wakeups. Follow
the [repository test isolation rules](../../AGENTS.md): use fake external resources,
keep the service-access guard enabled, and use in-process transports or temporary
files where needed. Do not perform wheel or package-installation smoke checks.

Test behavior and structural contracts, not documentation wording. Do not weaken
rules, add suppressions, or reduce coverage to make checks pass. Report unavailable
checks and unrelated existing failures with attempted commands and useful errors.

Finish with implemented behavior, areas changed, commands and results, findings
fixed, and remaining limitations. If a choice changes a premise needed by a later
phase, update that phase's guidance. Retain only necessary durable context, without
routine status logs, timestamps, commit identifiers, or session histories.

## Performance checkpoints

Use the [benchmark workflow](../../benchmarks/USAGE.md). Phase 01 captures the
baseline. Later phases identify useful checkpoints; choose representative subsets
and repeat measurements when needed to distinguish changes from noise. Compare
against the baseline and previous comparable checkpoint, retaining raw samples and
environment information in the suite's normal storage.

Adapt benchmark calls as APIs change, preserving equivalent workloads where
possible. Report changed semantics and meaningful comparison gaps; new capabilities
start their own histories. Unavailable transition measurements need not stop
implementation. Protect reuse and isolation with behavioral checks rather than
timing assertions or universal regression limits.
