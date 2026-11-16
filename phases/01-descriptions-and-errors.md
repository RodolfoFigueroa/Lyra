# Phase 1: Descriptions and errors

## Objective and prerequisites

Establish the public description and error contracts for retained location inputs.
Start from milestones 1–11. Read the [overview](index.md) and
[milestone 12](../feature-milestones/12-retained-location-inputs.md), especially
“Descriptions and local reads” and “Unavailable inputs and lookup errors.” Follow
the overview's verification and handoff rules.

This phase uses explicitly constructed retained fixtures. It does not claim that
executions already capture location inputs; phases 3–4 supply that integration.

## Implementation

Add a Pydantic location-input description with ordinary field access:

| Field | Contract |
| --- | --- |
| `kind` | `provider` or `correspondence`. |
| `table_schema` | Retained `OutputMetadata`, including logical types, nullability, units, and CRS. |
| `provider_scope` | The direct provider or correspondence target scope. |
| `source_scope` | Immediate source scope for a correspondence; `None` for a direct input. |
| `availability` | `available` or `not_materialized`. |

Reuse existing scope descriptions and their provider/code/origin identity rules.
Do not derive identities from frame attributes or add milestone-14 fields.
Extend public result descriptions and local metadata with `location_inputs`, keyed
by declared root location-input name. A root without such dependencies has `{}`.
Ordinary metric-table dependencies do not enter this mapping.

Add `ResultStore.describe_result(result_ref)` using the existing description-only
path. The current facade exposes `describe()`; adapt its implementation and
callers coherently rather than adding another metadata loader. Introducing the
required method does not require deleting existing supported access. Returned
descriptions are complete, independently owned, and usable after store closure.
Keep `read_result()` eager for metric outputs and the complete report; it must not
eagerly load location-input tables.

Add `LocationInputUnavailableError` under `ResultAccessError`, with `result_ref`
and `input_name`. Extend applicable lookup, pagination, and oversized-row errors
with optional `input_name`; location calls have `output_name=None`, while output
and whole-result calls have `input_name=None`. Whole-result lookups also have
`output_name=None`. Preserve existing output error payloads and constructor usage.

Extend shared error encoding/decoding for `location_input_unavailable` with HTTP
409, and retain the milestone's existing 404/422/500 categories. Pagination errors
retain `parameter`, oversized-row errors retain `download_url`, and clients must
eventually decode codes without parsing messages. Reuse strict reference and name
validation across local and transport boundaries; do not coerce non-string names
or introduce a new result-reference format. Input names resolve against retained
root declarations, not current plugins.

## Starting points

- [Public result models](../src/lyra/contracts/results.py),
  [errors](../src/lyra/contracts/errors.py),
  [error payloads](../src/lyra/contracts/retrieval_errors.py), and
  [references](../src/lyra/contracts/references.py).
- [Result facade](../src/lyra/runtime/result_store.py),
  [stored descriptions](../src/lyra/runtime/result_descriptions.py), and
  [local metadata](../src/lyra/runtime/results.py).
- [Provider scopes](../src/lyra/contracts/provider_scope.py) and
  [server description access](../src/lyra/server/results.py).

Private persistence structures belong to phase 2. Keep any fixture adaptation
small; do not invent a temporary serialized-frame storage format or publish
fabricated `not_materialized` states for unfinished paths.

## Verification and completion

Test model validation and error round trips, including optional input/output
context and informational message changes. Verify metadata-only access with
value loading, complete-report decoding, and plugin loading forbidden. Mutating
nested fields of one returned description must not affect another read. Include
known-empty and unavailable descriptions without requiring any table values.

Run the Python checks from the overview and relevant existing tests:

```sh
uv run pytest -n auto tests/test_result_descriptions.py tests/test_public_results.py tests/test_report_lookup.py tests/test_description_clients.py
```

Add focused tests for the new fields and errors. Update assertions about the
supported description shape while preserving their isolation and lookup checks.
Completion means the contract and metadata-only path work with retained fixtures;
runtime capture and transport endpoints are explicitly later work.

## Handoff

Record the chosen public model names, validation entry points, error signatures,
and description-method integration in [phase 2](02-selection-storage-and-local-reads.md).
Record any transport/client adaptation still needed in
[phase 9](09-http-and-mcp.md) or [phase 10](10-python-clients.md).
If fixture or execution tests fail because persisted input bindings do not exist
yet, name the exact failures and assign them to phases 2–4. Do not suppress them
or label their behavior as implemented.
