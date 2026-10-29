---
title: Connect an AI assistant with MCP
description: Discover a metric, submit once, check job status, and download the complete result.
contentType: How-to
contentPlan: use/mcp
sidebar: { label: "Connect an AI Assistant With MCP" }
---

The Model Context Protocol (MCP) exposes Lyra’s metric catalog and job workflow to an AI assistant. Connect your MCP client to the operator’s configured endpoint, such as `https://lyra.example.com/mcp/`, with the agent Bearer credential.

The transport uses the agent key and exposes consumer operations. The [tool reference](../../reference/generated/mcp/) gives exact contracts.

## 1. Discover a suitable metric

Search with `lyra_search_metrics`, or use `lyra_list_metrics` for an inventory. Inspect the selected metric with `lyra_get_metric` before choosing inputs.

Read `location_collections` in the `lyra_list_metrics` response for available collections, unit types, and coordinate reference systems (CRS).

## 2. Submit once

Call `lyra_run_metric` with `metric` and an `input` object matching the `request_schema` returned by `lyra_get_metric`. Always include `input`; use `{}` for a metric with no inputs.

For `smoke_table_metric`, submit the ID selection and metric parameters together. Replace the collection, unit type, and IDs with values supported by your deployment:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {
      "collection": "example:areas",
      "unit_type": "area",
      "ids": ["area-1"]
    },
    "parameters": {"value": 7}
  }
}
```

Put metric parameters under `input.parameters`. Include this object only when the request schema declares it.

For each named location input, supply a [collection, unit type, and nonempty ID list](../spatial-inputs/). Lyra rejects unknown IDs and reports the affected input. If the provider cannot fetch locations, submission returns `location_provider_unavailable` with retry guidance.

The submission response gives you a job ID and result reference:

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "next_tool": "lyra_get_job_result"
}
```

Lyra resolves locations before accepting the job. Every call creates an independent job. A timeout may happen after acceptance, so another submission can duplicate the calculation.

## 3. Observe the result

Call `lyra_get_job_result` with only `result_ref`. For `queued` or `running`, wait two seconds before the next inspection.

The tool call succeeds (`isError: false`) even when it reports a failed job. Check the job’s status and error before deciding what to do next. A submission that passes schema validation can still fail Python validation in its worker.

Successful inspection groups declarations, column summaries, and previews by named output. All outputs share a preview limit of five rows and twenty columns. Truncation metadata reports what the response omits or shortens. Column summaries include numeric bounds only when all non-null values are numeric.

Lyra limits the complete MCP response to 64 KiB, counting its text and structured copies. It may omit detailed metadata, previews, provenance, or errors to fit. Mandatory identifiers and output names remain; if those alone exceed the limit, the tool returns `result_response_too_large`.

## 4. Download the complete result

Call `lyra_download_result` with only `result_ref` to get the download URL. Send the agent Bearer credential when fetching that URL.

The download contains every named output of the requested metric, even if Lyra truncated the previews. Intermediate dependency tables stay internal. Fetch the descriptor URL for full output declarations and request provenance, which records the submitted inputs and plugin details.

Inspection summarizes each submitted ID selection as an object with a field count. Fetch the descriptor to read the collection, unit type, and IDs. Unknown metadata is null, and spatial preview coordinates use World Geodetic System 1984 (WGS84).

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
