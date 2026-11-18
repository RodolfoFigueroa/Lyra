# 13 · Share retrieval and polling in both Python clients

A caller retrieving every output from a successful job should receive the same frames as a caller downloading those outputs individually. Implement `results.tables(job_id)` in the sync and async clients using the Arrow download and decode path for `results.table(job_id, output_name)`. Keep the convenience method `result_tables(job_id)`.

## Bulk DataFrame retrieval

Read job status once. Propagate the status lookup's HTTP 404 for a missing or already expired job. Raise `JobNotReadyError` for a pending job and `MetricRunError` with the failure details for a failed job. For success, use the submitted job ID and the accepted metric name from status. Subsequent calls address durable storage by job ID, so expiry of the queue record after this point cannot interrupt retrieval.

Fetch the complete metadata with `results.describe(job_id)`. Before downloading any output, require its `job_id` and `metric_name` to match the submitted ID and accepted metric. Missing or mismatched identity fields are integrity errors. Download one Arrow stream per output in the verified description and return a dictionary keyed by output name. Propagate a failed download without returning a partial dictionary.

A successful job with N outputs costs one status GET, one description GET, and N Arrow download GETs, including for large descriptions. Each frame must match single-table retrieval in column order, logical types, values, nulls, geometry, and CRS. This includes typed empty tables and stores reopened without the producing plugin.

Remove the bulk JSON-to-DataFrame decoder. Include PyArrow in the `tables` extra so `lyra[client,tables]` supports both DataFrame methods. A `lyra[client]` installation still supports full JSON results through `results.get(job_id)`.

## Submission and polling

Within each client, share submission and waiting between `run()` and `run_to_file()`. Both submit once, start the polling deadline after submission, and wait for completion. Check the deadline immediately before and after the final result transfer. The transfer uses the client’s independent HTTP timeout and can finish after the polling deadline. Centralize deadline rules in `PollingState`. Keep native sync and async transport I/O and support awaitable progress callbacks in the async client. The final operation either decodes a result or streams it to the requested file.

Verify both clients with in-process transports. Cover the request counts, status errors, identity failures before any download, queue expiry after success, Arrow parity, and failure partway through multiple outputs. For the two run methods, cover a deadline that expires during the final transfer and raises `JobWaitTimeoutError` on the subsequent check, as well as successful decoding and file delivery. Exercise progress callbacks through the job handle's wait interface.
