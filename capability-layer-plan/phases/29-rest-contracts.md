# Phase 29 — REST contracts

Read the [shared workflow](index.md). Continue after
[phase 28](28-discovery-and-inspection.md). Apply ledger decisions **10, 15, 16, 24–27**.

Expose the completed shared contracts through REST, keeping routing and
preparation inside the core.

## Work

- Wire family discovery, family/concrete-edition inspection, and canonical-only
  submission to compiled catalog projections and shared remote acceptance.
- Update public request/response models and generated schemas to carry editions,
  named ID selections, ordinary parameters, and optional cache revision.
- Preserve job handles, polling, bounded previews, and authenticated full-result
  access. Ordinary results identify the public metric, selected edition, and
  output meaning; detailed provenance remains separately inspectable where
  appropriate.
- Map preparation failures to rejected submissions and accepted-job failures to
  failed job status. Successfully inspecting a failed job remains a successful
  HTTP inspection request.
- Replace superseded transport shapes and update necessary consumers. The next
  phase completes the Python client's public methods and workflow validation.

## Acceptance and validation

- Use in-process ASGI transports with fake storage, queues, and providers to cover
  discovery, inspection, submission, status, preview, and full-result retrieval.
- Test edition requirements, private-root rejection, invalid parameters and
  selections, unknown-ID job failure, unavailable revisions, and cache-hit context.
- Check response schemas, authentication, bounded results, and successful
  inspection of failed jobs without opening sockets.
- Run the index's Python checks and REST/result tests. Measure representative
  endpoint and serialization workloads.

## Boundary

Do not redesign client APIs here beyond necessary shared-contract adjustments.
Python client integration, MCP, and graph inspection have their own phases.
