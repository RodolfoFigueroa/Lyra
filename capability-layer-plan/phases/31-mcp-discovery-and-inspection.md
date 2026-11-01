# Phase 31 — MCP discovery and inspection

Read the [shared workflow](index.md). Continue after
[phase 30](30-python-client-workflows.md). Apply ledger decisions **11, 15, 24, 25**.

Expose family discovery and executable contract inspection through MCP before
completing job and result tool integration.

## Work

- Adapt list/search tools to one compact entry per family with public variants,
  including meaning, declared output granularity, and edition-selection rules.
- Preserve edition-specific accepted source availability in search and inspection.
  Do not advertise a source type for editions that cannot accept it.
- Distinguish a shared named-edition family contract from a concrete executable
  edition. Uneditioned inspection exposes its executable contract directly.
- Reuse compiled projections for schemas, parameter constraints, units,
  definitions, and structured accepted selections rather than reconstructing
  policy inside MCP.
- Update tool schemas and common guidance to explain named ID selections and
  canonical conversion once. Keep routine discovery independent of graph
  inspection and avoid generated route narratives.

## Acceptance and validation

- Test in-process tool dispatch and schema validation for implicit, single-named,
  and multi-edition families, private variants, and wholly private families.
- Verify accepted source availability and parameter contracts agree with shared
  inspection and preparation.
- Check compact response structures, existing ordering/pagination behavior, and
  relevant metadata without asserting guidance wording.
- Run the index's Python checks and MCP discovery/inspection tests. Measure
  representative tool dispatch and response size without network sessions.

## Boundary

Make only necessary adjustments to existing job tools when shared models change.
The next phase owns submission, polling, and result workflows. Do not add
operation discovery, route explorers, or dependency expansion.
