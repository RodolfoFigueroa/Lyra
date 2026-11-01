# Phase 33 — Shallow dependency inspection

Read the [shared workflow](index.md). Continue after
[phase 32](32-mcp-execution-and-results.md). Apply ledger decisions **11, 24, 25, 28**.

Provide one bounded, on-demand MCP lookup of a concrete metric variant's direct
declared dependencies and consumers.

## Work

- Resolve the requested concrete metric variant using established family/edition
  rules and inspect the compiled calculation graph.
- Return only direct neighbors, with relationship direction, relevant producer
  outputs, consumer input names, and private-node markers. Include location
  operations when they are declared calculation dependencies.
- Do not attach every possible canonical selection route as a metric dependency.
  Family association and corresponding nodes in other editions are metadata, not
  extra execution edges.
- Apply a finite response limit with deterministic ordering and an explicit
  indication of omitted relationships. Choose the concrete limit and response
  layout to fit existing MCP conventions.
- Keep private intermediates visible where relevant to relationships while
  preserving their exclusion from ordinary discovery and public execution.
- Guide agents to use this lookup for calculation structure or relationship
  questions. Routine execution, result interpretation, and actionable error
  responses must stand on their own.

## Acceptance and validation

- Test direct dependencies and consumers, several named outputs/inputs, private
  intermediates, explicit location operations, and concrete edition isolation.
- Verify a second-hop neighbor is not expanded and an available selection route
  is not misreported as a declared calculation dependency.
- Exercise empty neighborhoods and truncation above the chosen finite limit,
  checking explicit omission information and deterministic ordering.
- Run the index's Python checks and graph/MCP tests. Measure bounded response size
  and lookup cost for representative higher-degree nodes.

## Boundary

Do not add recursive expansion, path queries, canonical conversion-graph browsing,
job-plan exploration, traces, or administrative tools.
