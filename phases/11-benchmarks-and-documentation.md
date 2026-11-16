# Phase 11: Benchmarks and documentation

## Objective and prerequisites

Measure the completed feature, finish documentation, and close implementation
handoffs before the independent audit. Complete [phase 10](10-python-clients.md)
and read the handoffs across phases 1–10. Follow the [overview](index.md),
[milestone 12](../feature-milestones/12-retained-location-inputs.md), and the
[benchmark guide](../benchmarks/USAGE.md).

All runtime, local, transport, and client functionality should already work.
This phase does not introduce deferred core wiring, another storage representation,
or a final replacement of the execution path. If a handoff reveals missing feature
work, resolve it under its owning phase's contract before claiming completion.

## Performance work

Extend the existing ASV facilities with representative retained-input workloads:

| Workload | Measurements and observations |
| --- | --- |
| Initial direct and derived execution | Required write time, serialization/index costs, peak memory, and retained size. |
| Fully cached result publication | Reuse time and memory, with no input geometry loading to populate descriptions. |
| Mixed cached/fresh execution | Reference assembly, reduced acquisition, write cost for new values, and retained growth. |
| Many partitions sharing a large reference selection | Membership storage sharing and memory/write growth as partition count increases. |
| Dense correspondences with repeated targets | Pair growth, target-record deduplication, joined read/page/stream costs. |
| Repeated runs and changed revisions | Reuse of existing values versus distinct values under changed scopes or selections. |
| Description, page, and native/bulk reads | Metadata-only costs, bounded page behavior, and full-read memory proportional to requested data. |

Use repeatable in-process providers, fixed fixture data, and existing benchmark
profiles. Populate cached cases before measurement; reset only disposable benchmark
state for initial-write cases. Measure retained size including selection metadata,
not just provider row counts. Record meaningful comparable results where available,
and distinguish new workloads without a prior measurement from established cases.

Check the explicit design requirements: provider records are retained once per
existing identity; shared memberships are not copied per partition; geometries
are not stored in assembled correspondence tables; repeated results do not
unnecessarily duplicate values. Pair counts can exceed provider counts, and
changed revisions/selections legitimately add retained data. Indefinite retention
does not imply bounded growth or negligible overhead.

Follow the guide's existing-environment ASV commands. Select the new cases with
`--bench` when useful. A quick execution check is not a calibrated performance
comparison. Do not add wall-clock test thresholds, invented storage ceilings,
production dependencies, or wheel/install smoke checks. Standalone persistence
benchmarks may use disposable SQLite as the guide describes; pytest continues to
mock all database acquisition and keeps its service-access guard enabled.

## Documentation and acceptance closure

Update the relevant user-facing guides and executable examples to describe:

- Local descriptions and explicit native input reads, with output tables unchanged.
- Complete logical selections across cache reuse, shared references, and immediate
  correspondence roles, without public execution-history or ownership identities.
- Known-empty versus unavailable inputs, immutable availability, and integrity
  failures with no acquisition fallback.
- Sync/async client methods, bounded rows, authenticated Arrow downloads, complete
  description downloads, HTTP paths, and MCP tools.
- Retention costs and continued separation between persistence and cache policy.

Useful surfaces include [results](../docs/src/content/docs/use/results.md),
[local execution](../docs/src/content/docs/use/local-execution.md),
[Python clients](../docs/src/content/docs/use/python-client.md),
[REST](../docs/src/content/docs/use/rest-api.md),
[MCP](../docs/src/content/docs/use/mcp.md), and
[caching](../docs/src/content/docs/operate/caching.md). Check existing authoring
references and generated API surfaces for affected contracts. Keep author-facing
instructions current; remove contradicted prose rather than adding historical
comparisons. Keep READMEs short and free of architecture or validation procedures.

Review every milestone-12 completion requirement against the implementation and
behavioral coverage, including local/development/remote parity, changed active
registrations, job expiry, resource lifecycles, and persistence faults. Add missing
behavioral checks and fix bounded defects in the owning implementation area.
Do not move known failures to the read-only phase or add tests that pin prose.

## Verification and completion

Run the overview's Python checks after changing benchmark/test code, relevant
focused tests, and the full isolated suite with escalation and coverage:

```sh
uv run pytest -n auto --cov=lyra --cov-report=term-missing --cov-report=xml
```

Run the relevant ASV measurements from the benchmark guide. For changed site
documentation, use the existing documentation checks from `docs/package.json`
in the `docs/` directory, including executable-example checks where applicable.
Do not substitute documentation wording assertions for structural/link checks.

Report measurement scope, results, limitations, and any unavailable commands.
Completion requires all implementation handoffs resolved, required checks passing
or explicitly identified as environmental/pre-existing limitations, and no known
milestone feature gap hidden behind a successful test subset.

## Handoff

Update [phase 12](12-read-only-verification.md) with the actual new test paths,
relevant benchmark selectors, measurement/artifact locations, documentation checks,
and any environmental verification limitations. Keep useful evidence without
timestamps, commit inventories, or session transcripts. The verifier must be able
to reproduce checks and identify unsupported claims without asking how the feature
was implemented. Do not assign fixes to that read-only phase.
