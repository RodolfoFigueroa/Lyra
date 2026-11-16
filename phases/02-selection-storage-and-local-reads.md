# Phase 2: Selection storage and local reads

## Objective and prerequisites

Implement durable reconstruction references and local native reads. Complete
[phase 1](01-descriptions-and-errors.md) first and read its handoff, the
[overview](index.md), and [milestone 12](../feature-milestones/12-retained-location-inputs.md).
The key sections are “Required retention and cache independence” and “Storage and
inspection costs.” Follow the overview's verification rules.

Use retained fixtures to exercise the real storage/read implementation before
connecting execution. The fixtures mock database acquisition; they do not replace
the production selection and reconstruction logic being tested.

## Implementation

### Phase 1 handoff

- `lyra.contracts.results.LocationInputDescription` exposes `kind`,
  `table_schema: OutputMetadata`, `provider_scope: ProviderScope`, `source_scope`,
  and `availability`. Model validation requires a source scope for correspondence
  inputs and forbids it for direct provider inputs. `ExecutionMetadata`, public
  `ResultDescription`, and local `RunMetadata` expose `location_inputs` with an
  independent empty default.
- The private `runtime.result_descriptions.ResultDescription.location_inputs`
  currently carries explicitly constructed description fixtures. Its metadata
  projection deep-copies them. Replace this fixture-level attachment with the
  normalized selection references and declared input inventory owned by this
  phase; no input values or reconstruction references are stored by phase 1.
  `tests/result_description_helpers.py::location_descriptions` supplies direct,
  correspondence, known-empty, and unavailable metadata without acquiring values.
- `ResultStore.describe_result(result_ref)` uses the existing
  `ResultDescriptions.describe()` reader. `ResultStore.describe()` delegates to
  it, and `ResultService.read_description()` calls the new facade method.
  `read_result()` still eagerly reads metric tables and the complete report;
  its metadata includes location descriptions without reading input values.
- Reuse `contracts.references.RetrievalName` and
  `validate_retrieval_name(value)` for strict strings without coercion or
  normalization; resolve membership against the retained root inventory.
  `validate_result_reference(value, output_name=None, *, input_name=None)`
  preserves context and the existing 32-lowercase-hex reference format.
- Lookup errors accept `(result_ref, output_name=None, *, input_name=None)`.
  Pagination accepts `(result_ref, output_name, parameter, *, input_name=None)`;
  oversized rows accept `(result_ref, output_name, download_url, *, input_name=None)`.
  Pass `output_name=None` for input calls. The new
  `LocationInputUnavailableError(result_ref, input_name)` derives directly from
  `ResultAccessError`. Whole-result and output errors have `input_name=None`.

Phase 1 verification and final suite results are recorded in its handoff. Runtime
capture remains phases 3–4; the default empty mapping on current execution paths
does not assert that root location inputs have been captured.

Extend the existing relational store to associate each retained metric computation
with its named location-input reconstruction references. A final result selects
those references using existing computation keys and applicable groups. Provide
an explicit retained input inventory so missing descriptions can be distinguished
from undeclared names, including roots with no location dependencies.

Choose internal structures that represent:

- Captured schema and provider/source descriptions, including descriptions whose
  values are known empty or were never materialized.
- Direct selections of qualified provider records.
- Correspondence selections from completed operation groups, with enough selected
  membership to avoid leaking unrelated pairs from a larger computation.
- Complete shared selections referenced from multiple metric partitions without
  copying their membership lists into each partition.
- The published availability of each result input and the required relationships
  that make an available selection complete.

Reuse provider records and operation pairs under existing identities. Deduplicate
selection descriptions internally; do not expose a new public identity format.
Do not store assembled input frames, copied geometries per result, or historical
invocation/report links. The storage design must support selected groups and
target restrictions needed by later partial assembly, not just whole tables.

Make required values and references durable before a metric computation can be
accepted as completely retained. Its output completion and input-reference
publication must not leave an apparently reusable computation with missing
required bindings after a failure. Independently committed immutable provider or
operation values may survive a failed metric/result publication. Final result
description and successful report remain published together under the existing
durable-success contract. Do not put input geometry reads on that publication path.

Add `ResultStore.read_location_input(result_ref, input_name)`. Reconstruct direct
records or join selected immediate pairs to target records using retained logical
schemas, without a runner, plugin registration, or acquisition. Existing provider
lookup helpers depend on current registrations; introduce or adapt a retained
reader instead of reconstructing current plugins to satisfy those helpers.

Return independent GeoDataFrames with active geometry and CRS. Direct inputs have
unique `location_id`; correspondences have `source_location_id`, `location_id`,
and target attributes. Preserve repeated targets for different sources and add no
metric `row_key` or ownership. Known empty inputs return valid typed empty frames.
Unavailable inputs raise the new exception. Missing required records, pairs,
completion information, schemas, or relationships raise integrity errors.

Extend description projection and eager result metadata to use these retained
descriptions, without loading input values. Readers must survive writer closure,
reopening, and changed active registrations. Selection and join details should
remain reusable by phase 8's bounded SQL readers; do not make eager frames the
only internal way to select inputs.

## Starting points

- [SQLite layout](../src/lyra/runtime/sqlite_schema.py) and
  [store](../src/lyra/runtime/sqlite_store.py).
- [Provider records](../src/lyra/runtime/provider_records.py),
  [operation pairs](../src/lyra/runtime/operation_pairs.py), and
  [metric tables](../src/lyra/runtime/metric_tables.py).
- [Result descriptions](../src/lyra/runtime/result_descriptions.py),
  [output selection](../src/lyra/runtime/output_selection.py), and
  [result facade](../src/lyra/runtime/result_store.py).
- [SQL fake](../tests/sqlite_helpers.py) and
  [publication fixtures](../test_support/publication.py).

## Verification and completion

Exercise direct and correspondence fixtures, repeated targets, selected subsets,
known empty selections, completed zero matches, unavailable inputs, unknown names,
and ordinary metric dependency names. Include equal bare IDs in different scopes
and revisions, retained descriptions differing from current declarations, and
nullable native types. Test handler-independent stored values and caller mutation
isolation, including nested metadata and use after closure.

Inject missing data and failed transactions. Verify that no partial metric
completion or successful final result escapes, and that corruption cannot become
an empty frame or trigger acquisition. Check normalized retention and shared
membership structurally where needed to enforce the explicit storage requirement;
do not pin incidental helper calls or table names in public behavior tests.

Run the overview's Python checks and relevant coverage:

```sh
uv run pytest -n auto tests/test_sqlite_foundation.py tests/test_provider_records.py tests/test_operation_pairs.py tests/test_atomic_publication.py tests/test_result_store.py tests/test_native_result_retrieval.py
```

Completion means real local reads and descriptions work from retained fixtures
with isolated connections. Execution capture remains phases 3–4; cache assembly
remains phases 5–6. Historical-store migration is excluded.

## Handoff

Document the concrete storage relationships, completion boundary, selection APIs,
and required arguments in [phase 3](03-fresh-direct-inputs.md) and
[phase 4](04-operations-and-routes.md). Give [phase 8](08-sql-pages-and-arrow-streams.md)
the SQL selection/reconstruction entry points. Update receiving documents with
any failing tests or incomplete guarantees; do not require later sessions to infer
the meaning of newly introduced private structures.
