# Phase 9: HTTP and MCP

## Objective and prerequisites

Expose retained location inputs through the existing authenticated HTTP and MCP
interfaces. Complete [phase 8](08-sql-pages-and-arrow-streams.md), read its handoff
and phase 1's error contracts, and follow the [overview](index.md). Read “HTTP and
MCP access” and “Unavailable inputs and lookup errors” in
[milestone 12](../feature-milestones/12-retained-location-inputs.md).

The local reader, SQL pages, Arrow streams, and availability rules are complete.
Use the shared result service; do not duplicate storage reconstruction in routes
or tool handlers.

## Implementation

Add these read-only operations:

| Operation | HTTP | MCP |
| --- | --- | --- |
| Input rows | `GET /results/{result_ref}/location-inputs/{input_name}/rows?limit=100&cursor=...` | `lyra_get_location_input_rows(result_ref, input_name, limit=100, cursor=None)` |
| Input download | `GET /results/{result_ref}/location-inputs/{input_name}/download` | `lyra_download_location_input(result_ref, input_name)` |

Successful HTTP requests return 200. Downloads use
`application/vnd.apache.arrow.stream`. The download tool returns `result_ref`,
`input_name`, and an absolute `download_url` built from the deployment's public
API base URL. Download links require ordinary authentication; they are not public
or signed bypasses. Validate the input before issuing a successful link.

Extend `GET /results/{result_ref}`, `lyra_describe_result`, and
`GET /results/{result_ref}/description/download` to expose every retained output
and location-input description. Inspection returns the complete description only
if the encoded response fits. Otherwise omit the entire description and return
the existing fixed preview with `omitted=["description"]` and the complete JSON
download link. Do not truncate individual schemas or input lists. Large metadata
must not change availability or cause execution failure.

Enforce row and encoded-response limits through phase 8's common builder, using
each transport's complete envelope budget. MCP retains SDK-generated schemas,
argument validation, and protocol envelopes. Keep SDK argument-validation failures
separate from domain lookup errors.

Map domain failures consistently:

| Condition | HTTP | Code |
| --- | --- | --- |
| Malformed result reference | 422 | `invalid_result_reference` |
| Missing result or undeclared location-input name | 404 | `result_not_found` |
| Unavailable input | 409 | `location_input_unavailable` |
| Damaged required data or relationships | 500 | `result_integrity_error` |
| Invalid limit or cursor | 422 | `invalid_pagination` |
| Next row exceeds an empty JSON page budget | 422 | `row_too_large` |

Use shared bounded payloads with the applicable `input_name`, `result_ref`,
`output_name=None`, and pagination/download fields. The unavailable exception's
payload needs only its code/message and result/input identifiers. Existing output
and whole-result calls preserve their behavior and payload contracts. Do not
expose internal exception causes or require callers to interpret message text.

Use the existing asynchronous offloading and streaming response lifecycle.
Failures before streaming begins must produce structured errors, and cancellation
must release SQL readers and writers without abandoning an in-flight worker.
Read retained results independently of expired jobs or active plugin registrations.

## Starting points

- [Result service](../src/lyra/server/results.py),
  [HTTP routes](../src/lyra/server/routes/results.py), and
  [stream responses](../src/lyra/server/table_response.py).
- [MCP server](../src/lyra/server/mcp/server.py),
  [backend tools](../src/lyra/server/mcp/tools.py),
  [models](../src/lyra/server/mcp/models.py), and
  [serialization](../src/lyra/server/mcp/serialization.py).
- [Shared errors](../src/lyra/contracts/retrieval_errors.py),
  [paths](../src/lyra/contracts/retrieval_paths.py), and
  [response budgets](../src/lyra/server/response_budget.py).

## Verification and completion

Use in-process ASGI/MCP transports, temporary files, and mocked service/database
acquisition. Cover authentication, configured public URLs, both endpoints/tools,
SDK envelopes, and every error category. Check root metric dependency names are
not accepted as location inputs.

Force description previews with many input names and large schemas, including
known-empty and unavailable entries. Complete JSON downloads must retain every
field; description operations load no tables or complete report. Test bounded
pages, cross-interface cursors, oversized rows with Arrow fallback, valid empty
streams, and disconnect/cancellation cleanup.

Run the overview's Python checks and relevant tests:

```sh
uv run pytest -n auto tests/test_retrieval_transports.py tests/test_result_inspection.py tests/test_mcp_server.py tests/test_mcp_discovery.py tests/test_remote_relational_results.py
```

Add focused endpoint/tool tests and update affected API/MCP documentation to state
the supported contract. Completion means both transports expose the full retained
input interface, ready for the client methods.

## Handoff

Record endpoint specs, tool schemas, exact payload/error models, and description
fallback integration in [phase 10](10-python-clients.md). List documentation
surfaces still needing consolidated examples in
[phase 11](11-benchmarks-and-documentation.md). Transport omissions and resource
leaks belong here, not in the final verification phase.
