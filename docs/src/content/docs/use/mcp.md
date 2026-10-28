---
title: MCP workflow
description: Discover, submit once, inspect bounded results, and download retained data.
---

Connect to the configured MCP endpoint with `Authorization: Bearer` using the
credential in `LYRA_AGENT_API_KEY`. The [generated MCP reference](../../reference/generated/mcp/)
contains the strict input and serialization schemas.

## Discover, submit, inspect, download

1. Search with `lyra_search_metrics`; use `lyra_list_metrics` for catalog inventory.
   Read the selected contract with `lyra_get_metric`. Resolve a place name with
   `lyra_lookup_met_zone` when you need its raw metropolitan-zone code.
2. Call `lyra_run_metric` with `metric`, `met_zone_code`, optional `parameters`,
   only. Submission never waits or reads completion.
3. Call `lyra_get_job_result` with only `result_ref`. For `queued` or `running`,
   wait two seconds before the next inspection. Failed jobs are
   successful observations (`isError: false`) with terminal status.
4. Call `lyra_download_result` with only `result_ref` to obtain an authenticated
   HTTP handoff. Results use
   `/jobs/{job_id}/result/download`. Send the agent Bearer credential yourself.

Every submission creates an independent job. A timeout or transport failure may
occur after acceptance; another submission may duplicate the computation. There
is no automatic resubmission or remote cancellation. Observation retries are bounded.

## Metric input boundaries

`lyra_run_metric` nests its `parameters` argument under `input.parameters` and
resolves `met_zone_code` for the metric's single spatial field. For example:

```json
{"metric":"smoke_table_metric","met_zone_code":"09.01","parameters":{"value":7}}
```

The helper supplies `{}` for a declared parameter model when no parameters are
provided; required fields still need values. For a parameterless metric it omits
the property, and rejects nonempty parameters instead of discarding them.

Metrics with no spatial inputs (including fixed-region metrics), or with both
`location` and `bounds`, are discoverable but execution through this helper
returns `unsupported_spatial_shape`. Use REST or the Python client
for those metrics. A schema-valid submission can later fail with `invalid_input`
when the worker runs Python semantic validators.

## Inspection limits and complete metadata

Inspection groups metadata, column summaries, and previews by named root output.
Previews share a limit of five rows and twenty columns across all outputs. Each
output reports omitted rows, omitted columns, and shortened strings. Numeric
bounds apply only when all non-null column values are numeric; dates remain ISO
strings. Spatial metadata describes public WGS84 coordinates. Request provenance
does not assign identity to output rows. Authenticated descriptor access provides
complete metadata. The complete serialized MCP response is limited to 64 KiB,
counting both text and structured copies. Previews, output metadata, input details, and detailed
errors may be omitted explicitly; mandatory identifiers and output names remain. If these exceed the limit, the
tool returns `result_response_too_large`.

Compact provenance retains metric/plugin identity, catalog fingerprint, and
creation time. Collections become type/count summaries and GeoJSON becomes a
feature count and CRS without coordinates. Use the descriptor handoff to retrieve
full request provenance and named output declarations.

Unknown timestamps or provenance are null. Retained terminal payloads take
precedence over stale or missing status. Failed status without a payload
still reports that known outcome without inventing result metadata.

## Representative payloads

These are the shared payloads in `structuredContent` and the JSON text content;
optional null fields may also be present. Authentication instructions name an
environment variable and never contain its secret value.

Submission:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","next_tool":"lyra_get_job_result"}
```

Queued:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"queued","metric":"population","created_at":"2026-09-24T12:00:00Z","updated_at":"2026-09-24T12:00:00Z","poll_after_seconds":2,"next_tool":"lyra_get_job_result","truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Running with progress:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"running","metric":"population","created_at":"2026-09-24T12:00:00Z","updated_at":"2026-09-24T12:00:03Z","started_at":"2026-09-24T12:00:01Z","progress":{"timestamp":"2026-09-24T12:00:03Z","stage":"compute","current":1,"total":10},"poll_after_seconds":2,"next_tool":"lyra_get_job_result","truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```

Table success without retained provenance:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"succeeded","result_kind":"table","completed_at":"2026-09-24T12:00:10Z","lifetime":{"expires_in_seconds":3600},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/job-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]},"outputs":{"population":{"metadata":{"row_count":1,"declaration":{"kind":"table","columns":[],"geometry":null},"spatial":false,"column_count":1,"summary":{"population":{"null_count":0,"non_null_count":1,"minimum":100,"maximum":100}}},"preview":[{"population":100}],"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}}}
```

Failure (`isError: false`):

```json
{"job_id":"failed-1","result_ref":"lyra://results/failed-1","status":"failed","result_kind":"failed","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/failed-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"error":{"message":"Execution failed"},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}
```


Unknown or expired reference (`isError: true`):

```json
{"error":{"code":"result_not_found","message":"This result reference is unknown or expired.","details":{"job_id":"old-1","result_ref":"lyra://results/old-1"}}}
```

JSON download handoff:

```json
{"job_id":"job-1","result_ref":"lyra://results/job-1","status":"succeeded","format":"json","media_type":"application/json","lifetime":{"expires_in_seconds":3600},"lyra_api":{"method":"GET","url":"https://lyra.example/api/jobs/job-1/result/download","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}}}
```


Multiple named outputs (ordinary and spatial values are available in the JSON result):

```json
{"job_id":"mixed-1","result_ref":"lyra://results/mixed-1","status":"succeeded","result_kind":"table","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/mixed-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"outputs":{"summary":{"metadata":{"row_count":1,"declaration":{"kind":"table","columns":[],"geometry":null},"spatial":false,"crs":null,"geometry_types":[],"column_count":1,"summary":{"count":{"null_count":0,"non_null_count":1,"minimum":1,"maximum":1}}},"preview":[{"count":1}],"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}},"parcels":{"metadata":{"row_count":1,"declaration":{"kind":"table","columns":[],"geometry":"geometry"},"spatial":true,"crs":"EPSG:4326","geometry_types":["Point"],"column_count":1,"summary":{"count":{"null_count":0,"non_null_count":1,"minimum":2,"maximum":2}}},"preview":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-99,20]},"properties":{"count":2}}]},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}}}
```

Request provenance alongside named output inspection:

```json
{"job_id":"mixed-1","result_ref":"lyra://results/mixed-1","status":"succeeded","result_kind":"table","lifetime":{},"descriptor":{"method":"GET","url":"https://lyra.example/api/jobs/mixed-1/result/descriptor","authentication":{"scheme":"Bearer","credential_env_var":"LYRA_AGENT_API_KEY"}},"outputs":{"summary":{"metadata":{"row_count":1,"declaration":{"kind":"table","columns":[],"geometry":null},"spatial":false,"crs":null,"geometry_types":[],"column_count":1,"summary":{"count":{"null_count":0,"non_null_count":1,"minimum":1,"maximum":1}}},"preview":[{"count":1}],"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}},"parcels":{"metadata":{"row_count":1,"declaration":{"kind":"table","columns":[],"geometry":"geometry"},"spatial":true,"crs":"EPSG:4326","geometry_types":["Point"],"column_count":1,"summary":{"count":{"null_count":0,"non_null_count":1,"minimum":2,"maximum":2}}},"preview":{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-99,20]},"properties":{"count":2}}]},"truncation":{"omitted_rows":0,"omitted_columns":0,"shortened_strings":0,"omitted_sections":[]}}},"provenance":{"metric":"built_form","catalog_fingerprint":"catalog-1","plugin":{"name":"city","version":"1"},"created_at":"2026-10-27T00:00:00Z","input":{"location":{"type":"FeatureCollection","feature_count":1,"crs":"EPSG:4326"}}}}
```

## Expiration and errors

Copy results before their reported lifetime ends. Active jobs have no terminal
retention deadline. An unknown or expired reference returns `result_not_found`;
there are no tombstones to distinguish the two. A succeeded status whose payload
is missing returns `result_unavailable`.

Downloading an active job returns `result_not_ready` with two-second polling
guidance. Failed downloads return `result_not_downloadable`.
Infrastructure and deadline errors are retryable tool errors. These errors never
cause an automatic submission. If retained data is gone, decide whether the user
still needs a new execution before submitting one.

Output names in summaries and previews identify tables published by the requested
metric. Dependency tables stay internal; clients submit the public request once
and Lyra executes its graph. Downloads contain the complete named-output JSON
result even when a preview is truncated.
