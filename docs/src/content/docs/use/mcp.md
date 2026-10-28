---
title: Connect an AI assistant with MCP
description: Discover a metric, submit once, observe the job, and obtain a complete-result download.
contentType: How-to
contentPlan: use/mcp
sidebar: { label: "Connect An Ai Assistant With MCP" }
---

The Model Context Protocol (MCP) exposes Lyra’s metric catalog and job workflow to an AI assistant. Connect your MCP client to the operator’s configured endpoint, such as `https://lyra.example.com/mcp/`, with the agent Bearer credential.

The transport uses the agent key and exposes consumer operations. The [tool reference](../../reference/generated/mcp/) gives exact contracts.

## 1. Discover a suitable metric

Search with `lyra_search_metrics`, or use `lyra_list_metrics` for an inventory. Inspect the selected metric with `lyra_get_metric` before choosing inputs.

For a place name, call `lyra_lookup_met_zone` and use the returned raw metropolitan-zone code.

## 2. Submit once

Call `lyra_run_metric` with `metric` and an `input` object matching the `request_schema` returned by `lyra_get_metric`. Always include `input`; use `{}` for a metric with no inputs.

For `smoke_table_metric`, submit the metropolitan-zone selector and metric parameters together:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"value": 7}
  }
}
```

Put metric parameters under `input.parameters`. Include this object only when the request schema declares it.

For each spatial field in the request schema, supply one of these [spatial selectors](../spatial-inputs/):

- `geojson`: a GeoJSON polygon collection
- `cvegeo_list`: a list of census geographic identifiers
- `met_zone_code`: a metropolitan-zone code

You can submit GeoJSON or nonspatial inputs without PostgreSQL; geographic-code selectors require a configured database. The metric or its dependencies may still need PostgreSQL during execution.

The submission response gives you a job ID and result reference:

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "next_tool": "lyra_get_job_result"
}
```

Submission returns immediately. Every call creates an independent job. A timeout may happen after acceptance, so another submission can duplicate the calculation.

## 3. Observe the result

Call `lyra_get_job_result` with only `result_ref`. For `queued` or `running`, wait two seconds before the next inspection.

The tool call succeeds (`isError: false`) even when the job fails, because it successfully reports a terminal outcome. Check the job’s status and error before deciding what to do next; a schema-valid submission can still fail Python validation in its worker.

Successful inspection groups declarations, column summaries, and previews by named output. The preview budget is shared across outputs: five rows and twenty columns. Truncation metadata reports omitted rows, columns, shortened strings, and sections. Numeric bounds are supplied only when all non-null values in a column are numeric.

The complete MCP response is limited to 64 KiB, counting its text and structured copies. Detailed metadata, previews, provenance, or errors can be omitted to fit the budget. Mandatory identifiers and output names remain; if those alone exceed the limit, the tool returns `result_response_too_large`.

## 4. Download the complete result

Call `lyra_download_result` with only `result_ref`. It returns an authenticated HTTP handoff to the result download route. Your client sends the agent Bearer credential when fetching that URL.

The download contains every named output of the requested metric, even if previews were truncated. Intermediate dependency tables stay internal. Use the descriptor handoff for full declarations and request provenance.

Inspection provenance is compact: collections become type/count summaries and GeoJSON includes feature count and coordinate reference system (CRS) without coordinates. Unknown metadata is null. Spatial preview coordinates use World Geodetic System 1984 (WGS84).

## Handle unavailable results

An unavailable result can mean that work is still active, retained data has expired, or execution failed. Use the error code to distinguish these cases:

| Error | Meaning |
| --- | --- |
| `result_not_found` | The reference is unknown or expired |
| `result_unavailable` | The job succeeded, but its retained payload is missing |
| `result_not_ready` | A download was requested while work was active |
| `result_not_downloadable` | The job failed |

Active jobs have no terminal retention deadline. Download completed data before its reported lifetime ends. Retained terminal payloads take precedence over stale or missing status observations.

Infrastructure and deadline failures can be retryable tool errors. Observation retries are bounded; they do not trigger resubmission. Decide whether a new execution is needed when retained data is gone.

See [payload examples](../../reference/mcp-payloads/) for complete formatted responses and [results](../results/) for table interpretation.
