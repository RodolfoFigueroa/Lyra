# Phase 10: Python clients

## Objective and prerequisites

Provide matching synchronous and asynchronous client access to retained location
inputs. Complete [phase 9](09-http-and-mcp.md) and read its transport/error handoff.
Follow the [overview](index.md), [milestone 11](../feature-milestones/11-result-and-report-retrieval.md),
and the remote-table examples in
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

Endpoints, complete description downloads, shared errors, and Arrow streams are
already implemented. Extend the clients' existing request and download policies
instead of adding an alternate HTTP stack or file writer.

## Implementation

### Phase 1 handoff

Both existing `results.describe(result_ref)` clients validate the extended
`contracts.results.ResultDescription`, including `LocationInputDescription`
entries. `tests/test_description_clients.py` covers complete and preview/download
responses with direct, correspondence, known-empty, and unavailable metadata,
mutation independence, and usability after client closure. The shared error
decoder already handles HTTP 409 `location_input_unavailable` and optional
`input_name` on lookup/pagination/oversized-row failures without parsing messages.

This phase still owns all three location-input retrieval methods, their endpoint
policies, and method-level error/lifecycle tests. Reuse `RetrievalName` and
`validate_retrieval_name` from `contracts.references`; input calls use
`output_name=None` in shared errors. Phase 1's handoff records verification results.

Expose these methods through both clients' `results` interfaces:

| Method | Return contract |
| --- | --- |
| `describe(result_ref)` | Complete description, including every location input. |
| `location_input(result_ref, input_name)` | Independent GeoDataFrame. |
| `location_input_rows(result_ref, input_name, limit=100, cursor=None)` | Existing `rows`/`next_cursor` page shape. |
| `download_location_input(result_ref, input_name, destination)` | Write Arrow IPC to a local `str` or `Path`; return `None`. |

`AsyncLyraClient` provides the same arguments and return values using `await`.
Use the same strict string validation and endpoint/error policies in both clients.
Map errors by shared codes and structured fields, including unavailable inputs,
optional input/output context, pagination `parameter`, and oversized-row
`download_url`. Do not parse message text or treat HTTP 409 as a retry that can
materialize the input.

When description inspection returns a preview, fetch its complete JSON download
automatically. The Python description API must never return a truncated input
inventory or a preview in place of the full description. Local description reads
remain complete independently of transport limits.

Use the input Arrow endpoint for both native frame reads and downloads. Reuse
GeoArrow WKB decoding and retained logical schema reconstruction, preserving
nullable types, active geometry, and CRS even for known-empty inputs. Frames and
nested descriptions must be independently owned and remain usable after closing
the client. Do not keep response iterators or client-owned buffers attached to
returned native values.

Reuse milestone 11's download behavior: validate errors before damaging a
destination, write through a temporary file, replace the destination only after
successful completion, and clean up on transport failure or cancellation.
Preserve ordinary filesystem exceptions rather than wrapping them as result
lookup failures. Do not add persistent Arrow artifacts to the server store.

Update Python-client usage documentation and examples with the implemented
methods. Keep examples about logical input selections and explicit reads, without
exposing internal selection identities or storage details.

## Starting points

- [Sync client](../src/lyra/api/client/sync.py),
  [async client](../src/lyra/api/client/async_.py), and
  [endpoint specifications](../src/lyra/api/client/endpoints.py).
- [Arrow decoding](../src/lyra/api/client/_arrow.py),
  [table policies](../src/lyra/api/client/_tables.py), and
  [download policies](../src/lyra/api/client/results.py).
- [Shared errors](../src/lyra/contracts/retrieval_errors.py),
  [client test helpers](../tests/retrieval_client_helpers.py), and
  [download helpers](../tests/arrow_download_helpers.py).

## Verification and completion

Run the same behavioral scenarios against sync and async clients using mocked
HTTP clients or in-process transports. Cover direct inputs, repeated-target
correspondences, empty frames, unavailable inputs, complete descriptions after
preview fallback, pagination, and every shared error category and attribute.

Test equal input/output names with wrong-interface cursors, mutation independence
across reads, and post-close usability. Check download replacement, existing-file
preservation on failure, temporary-file cleanup, `str`/`Path` destinations,
ordinary filesystem failures, cancellation during transfer and writing, and
response cleanup. Preserve existing output and report client behavior.

Run the overview's Python checks and relevant coverage:

```sh
uv run pytest -n auto tests/test_description_clients.py tests/test_table_clients.py tests/test_arrow_downloads.py tests/test_async_client_cancellation.py tests/test_native_client_acceptance.py tests/test_client_imports.py
```

Add focused new method tests to these shared scenarios or appropriate new modules.
Completion means the full user-facing milestone interface works in both clients,
with no remaining transport or runtime integration reserved for phase 11.

## Handoff

Record implemented method signatures, new shared fixtures, commands run, and any
remaining documentation or measurement work in
[phase 11](11-benchmarks-and-documentation.md). Fix client correctness and lifecycle
defects in this phase. Do not mark it complete with unimplemented async parity or
download cleanup left for the read-only audit.
