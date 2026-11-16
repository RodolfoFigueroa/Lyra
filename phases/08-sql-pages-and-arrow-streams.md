# Phase 8: SQL pages and Arrow streams

## Objective and prerequisites

Provide bounded location-input row selection and generated Arrow streams for the
transport layer. Complete [phase 7](07-availability-and-skipped-work.md) and read
its handoff plus the selection APIs established in phase 2. Follow the
[overview](index.md), [milestone 11](../feature-milestones/11-result-and-report-retrieval.md),
and the pagination/download requirements in
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

Local descriptions and native input reads should already be complete. Extend the
existing selection, encoding, and streaming machinery; do not implement public
HTTP/MCP handlers in this phase.

## Implementation

Expose internal input-selection operations that fetch bounded SQL batches from
retained provider records or selected correspondence joins. Respect selected
groups and any target restrictions carried by the binding. Do not call eager
`read_location_input()` and slice its entire frame to implement a page or stream.
Retain appropriate indexed access for selection and continuation.

Adapt the shared row-page machinery to preserve milestone 11's JSON cell encodings,
row-count limits, and actual encoded-response budgets. Emit complete rows only.
The next cursor advances only past returned rows, including when a prefetched row
does not fit. If the next row cannot fit in an otherwise empty page, raise
`RowTooLargeError` with the input's complete Arrow download URL, preserving the
request's traversal position.

Cursors must bind to the result reference, input name, and location-input
interface. Reject wrong-result, wrong-input, named-output, malformed, and invalid
position cursors. A matching output name does not make an output cursor valid for
an input. Validate limits strictly under existing rules and retain `parameter`
on pagination errors. Evolve shared cursor internals without changing supported
named-output behavior or introducing public row identities.

Traverse each selected direct record or correspondence pair once. Shared targets
under distinct sources remain separate rows; identical qualified pairs selected
through overlapping references must not be emitted repeatedly. Use a deterministic
private continuation mechanism without promising public row order.

Generate Arrow IPC streams from bounded SQL batches using the retained logical
schema and standard GeoArrow WKB geometry encoding. Known empty inputs produce
valid streams containing their declared schema and CRS. Native reads and remote
downloads must share these type/geometry semantics. Do not store Arrow artifacts
or permanent joined forwarding tables.

Keep selection, reader, writer, and connection ownership explicit. Close resources
on exhaustion, early consumer exit, encoding failure, and cancellation. Validate
lookup and availability before a transport can commit a successful stream response,
following the existing output-stream preflight lifecycle. Never repair an input
by acquiring providers, following reports, or invoking handlers.

## Starting points

- [Output selection](../src/lyra/runtime/output_selection.py),
  [pagination](../src/lyra/runtime/pagination.py), and
  [result facade](../src/lyra/runtime/result_store.py).
- [Row-page builder](../src/lyra/server/result_rows.py),
  [response budgets](../src/lyra/server/response_budget.py), and
  [retrieval paths](../src/lyra/contracts/retrieval_paths.py).
- [Arrow tables](../src/lyra/data/arrow_tables.py),
  [relational codecs](../src/lyra/data/relational.py), and
  [stream response ownership](../src/lyra/server/table_response.py).
- [SQL fake](../tests/sqlite_helpers.py), extending only the needed query behavior
  while keeping database acquisition mocked.

## Verification and completion

Test bounded selection without eager native reads, complete traversal, selected
subsets, repeated targets, overlapping references, known empties, unavailable
inputs, and damaged required data. Test cross-interface/name/result cursor
rejection and continuation across encoded-byte boundaries. Verify oversized-row
errors do not silently consume or truncate a row.

Round-trip Arrow for nullable logical types, geometry, CRS, and empty schemas.
Verify cleanup after a consumer stops early and after faults. Description and
page reads must remain independent of metric-output values and historical reports.
Keep existing named-output pagination and Arrow tests passing.

Run the overview's Python checks and relevant tests:

```sh
uv run pytest -n auto tests/test_result_pagination.py tests/test_result_row_pages.py tests/test_arrow_conversion.py tests/test_arrow_geometry.py tests/test_arrow_properties.py
```

Add focused input-selection/stream tests. Completion means the shared service can
obtain bounded rows, validate download links, and generate complete input streams
without any new public endpoint yet.

## Handoff

Record selection/page/stream method signatures, cursor interface discrimination,
download-path helpers, and cleanup obligations in [phase 9](09-http-and-mcp.md).
Give [phase 10](10-python-clients.md) the Arrow metadata/decoding expectations.
If shared pagination changes expose existing output failures, fix regressions
here rather than postponing them to transport wiring.
