# 12 · Simplify retained-result retrieval

Callers can describe a retained result by job ID, inspect a bounded sample of metric output, or download complete values. Each operation should read only the data it needs. Descriptions and named table downloads use durable storage directly. Job-oriented JSON retrieval and the MCP job download handoff use the queue record to establish job state.

## Descriptions, JSON, and inspection

Authenticated `GET /results/{job_id}` returns the complete `ResultDescription` in one request at any metadata size. Build the description from metadata alone, including each retained location input's schema, provider and applicable source scopes, and availability. Both Python clients' `results.describe()` fetch and validate the complete description in one GET. Remove the HTTP preview wrapper and separate description download route. Keep the 64 KiB budget on MCP's `lyra_describe_result`. Return the full description when it fits, otherwise a preview with a `download_url` pointing to the same authenticated endpoint under the configured public path prefix.

For full job JSON and JSON downloads, verify the successful job's retained ID and metric name against its job record, read its tables once, and serialize them directly. Encode integers as JSON numbers, Booleans as booleans, and missing values as null. Use WGS84 GeoJSON for spatial output, where both empty and missing geometry can serialize as null. Preserve HTTP 202 for pending jobs and structured terminal results for failures.

The MCP job download tool verifies that the successful queue record's ID matches the request and returns the authenticated URL and expiry information from that record alone. A missing, malformed, or mismatched identifier prevents the handoff. Keep pending, failed, and missing or expired job errors. Integrity issues in retained rows surface when inspection or download actually reads the data.

Compute previews and column statistics only for metric outputs requested through HTTP or MCP inspection, within the existing response budget. Use registered dtypes to select integer and floating-point columns. Use pandas `count` for populated values and derive null count from row count. Use `min` and `max` on populated numeric columns, preserving exact large integers. Boolean and all-null columns have null extrema.

## Named Arrow downloads

Use fixed download paths with a required string query parameter, `output_name` for a metric output or `input_name` for a location input. Encode names with `urllib.parse.urlencode`. Preserve authentication, public path prefixes, and exact lookup, including an empty string, `..`, slashes, newlines, and Unicode punctuation. Switch server routes, both clients, descriptors, and MCP links together. Remove the custom path converter and dot-segment handling. An existing path-based link needs to be fetched again from the updated interface.

Remove the two retained `/rows` routes, the MCP tools `lyra_get_result_rows` and `lyra_get_location_input_rows`, and both clients' `results.rows()` and `results.location_input_rows()`. Remove row-page models, cursors, sizing logic, page-only errors, the oversized-row fallback, and dedicated examples, tests, and benchmarks. Direct callers to bounded output inspection or local filtering of a named Arrow download. Catalog listing continues to support offset pagination.

For a complete metric output, verify completion and run one ordered SELECT per selected computation. Consume bounded batches with SQLAlchemy's `mappings().partitions(batch_size)`. Remove the per-row `RowAddress` and continuation predicate. Preserve selected groups, row order, duplicates, and empty-output schemas. Correspondence inputs still merge rows from multiple selections. Keep the cursor open while yielding its batches and release it on exhaustion, early close, or error.

Stage each HTTP Arrow export in one synchronous operation off the event loop. That operation owns the reader, encodes bounded batches, and closes the reader before Starlette's `FileResponse` serves the completed file. Release the request's temporary file on success, error, or disconnect. A disconnect during staging can allow staging to finish before cleanup. Malformed WKB or missing rows must yield a structured integrity error before HTTP success headers.

## Errors and verification

Remove conditional field serialization from shared lookup errors and emit both `output_name` and `input_name`. A whole-result failure sets both to null. A named lookup sets the requested name and leaves the other null. An authenticated download request for an existing result with the required name parameter omitted receives HTTP 422. A request with `?output_name=` looks up the empty string and receives a structured HTTP 404 with `output_name=""` and `input_name=null`, since retained output names must be nonblank. For a valid job ID and an unknown output named `population`, verify that the HTTP 404 decodes with `output_name="population"` and `input_name=null`. Cover the corresponding named-input and whole-result cases. Keep the required input name on `location_input_unavailable` and verify agreement between HTTP status and error code.

Use in-process transports and isolated readers to exercise a large description fetched in one GET by each client and followed through its MCP preview link. Cover exact-name downloads, bounded fetches, cursor and temporary-file cleanup, and an integrity error during staging. Check full JSON types and inspection extrema separately. Update the retrieval guide, HTTP contract, and MCP examples to show the supported calls.
