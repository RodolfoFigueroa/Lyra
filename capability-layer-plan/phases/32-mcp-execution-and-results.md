# Phase 32 — MCP execution and results

Read the [shared workflow](index.md). Continue after
[phase 31](31-mcp-discovery-and-inspection.md). Apply ledger decisions **10, 11, 15, 16, 26, 27**.

Complete MCP execution and result workflows using the shared acceptance and
result components.

## Work

- Submit public metrics, required editions, declared parameters, named source ID
  selections, and optional cache revision through shared preparation/acceptance.
  Reject caller-selected operations and pipelines.
- Preserve remote job handles, polling, bounded previews, and authenticated
  full-result handoff. Ordinary result views identify metric, edition, and
  output meaning without requiring graph inspection.
- Reuse accepted bindings and current execution provenance in result projections.
  Do not report planned dependencies as executed or reuse an earlier request's
  provenance on a cache hit.
- Map preparation failures to actionable tool failures. Report accepted-job
  execution failures as job state; successfully inspecting that state is a
  successful tool invocation.
- Complete the discovery-to-execution flow with the preceding phase's tools and
  update shared guidance without duplicating route or parameter policy.

## Acceptance and validation

- Test submission through status, bounded preview, and full-result handoff using
  in-process dispatch and fake services.
- Cover missing editions, private-root rejection, unsupported selections,
  invalid parameters, unknown IDs, empty conversions, unavailable revisions,
  and fresh cached-result context.
- Verify semantic agreement with local and REST behavior and successful inspection
  of failed jobs. Check response bounds without pinning guidance prose.
- Run the index's Python checks and MCP/result tests. Measure dispatch and
  serialization workloads without protocol-session network traffic.

## Boundary

Do not add administration, per-node traces, or job-plan explorers. The next phase
adds the single bounded shallow dependency lookup.
