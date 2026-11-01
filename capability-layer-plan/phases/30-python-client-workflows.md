# Phase 30 — Python client workflows

Read the [shared workflow](index.md). Continue after
[phase 29](29-rest-contracts.md). Apply ledger decisions **10, 15, 24–27**.

Adapt the Python API client to the completed REST contract and verify both
synchronous and asynchronous workflows.

## Work

- Expose compact family discovery, family/edition inspection, and canonical-only
  submission with required editions, named ID selections, ordinary parameters,
  and optional cache revision.
- Reuse the established transport models and semantic errors rather than
  independently validating routes or choosing editions.
- Carry job handles through polling, bounded result inspection, and authenticated
  full-result retrieval. Preserve metric/edition identity, output metadata, and
  available provenance.
- Keep sync and async behavior consistent, including a successful status lookup
  that reports a failed job.
- Replace superseded client shapes directly and update affected usage examples.
  Preserve unrelated client authentication, retry, and polling policy.

## Acceptance and validation

- Exercise sync and async discovery-to-result workflows through mocked transport
  boundaries or in-process adapters. Do not open HTTP connections.
- Cover explicit and implicit editions, private-root and invalid-request
  rejection, terminal execution failures, empty outputs, and cached-result context.
- Verify parameter defaults are accepted from the server contract without client
  replanning, and full-result access retains its authentication behavior.
- Run the index's Python checks and affected client/polling/result tests. Reuse
  existing fixtures rather than duplicating core execution coverage.

## Boundary

Do not change server semantics or add compatibility aliases, operation pipelines,
or administrative client features. MCP integration follows next.
