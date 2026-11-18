# 10 · Identify each run and its results by job ID

Give the caller one identifier at submission. The same `job_id` identifies queue status and failure details, every node context, and the durable report, description, and tables. A new submission receives a fresh ID even when every computation is cached.

## Generation, validation, and lifetime

Use `uuid4().hex` and a shared `JobId` contract of exactly 32 lowercase hexadecimal characters. The worker verifies job identity, build revision, and the accepted plan before executor preflight. `LocalRunner` generates the ID once at the start of each run and creates no queue entry. Remove the public `context_factory` argument from the runner and `from_definitions`. Adapt its callers and the local execution guide, retaining spatial-output and resource-cleanup coverage.

Use ordinary FastAPI path routing across public and admin job routes, retained results, and reports. Remove custom job-ID routing and opaque-ID URL handling. On a matched route, an authorized request with a malformed ID receives the standard HTTP 422 identifying the `job_id` path parameter before a queue or storage lookup. A valid ID with no corresponding resource receives HTTP 404. Both Python clients validate before transport and MCP validates before calling its backend. Preserve authorization before lookup. Remove the `invalid_result_reference` and `invalid_execution_id` error variants and their codecs.

Publish at most one report and result set per submission in one SQLite commit under `job_id`. A pre-commit failure exposes neither. Once the commit succeeds, direct retained lookups by job ID work even if the worker stops before RQ records success or the queue record later expires. The status endpoint still reflects the lifetime of the queue record.

## Retained interfaces and exports

Replace `execution_id` and `result_ref` with `job_id` throughout storage, SDK models, local accessors, HTTP routes, MCP tools, and both Python clients. Select individual tables by job ID and exact name. Update retrieval examples, fixtures, and contract references together. The closed-alpha storage change permits recreating local SQLite stores.

Keep both GeoParquet export methods on the local result store and on each client. Each fetches its frame and retained description through its own interface. Centralize table selection and construction of `attrs["lyra"]` beside `write_geoparquet`. Use `job_id` as the exported identifier. For `kind="metric_output"`, include `output_name` and the complete selected output description. For `kind="location_input"`, include `input_name` and its complete description, including source and target provider scopes for correspondence inputs. Replace the destination atomically on success.

Verify that one ID follows a local or queued run from submission to report and named table, including a full cache hit. Replace opaque-ID routing cases with valid, malformed, and missing IDs and authorization before lookup. Cover resubmission, failed publication, and direct reads after commit both before RQ records success and after queue expiry. Check the exported metadata for both table kinds through the local, sync, and async interfaces.
