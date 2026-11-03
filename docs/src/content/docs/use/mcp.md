---
title: Connect an AI assistant with MCP
description: Discover a metric, submit once, check job status, and download the complete result.
contentType: How-to
contentPlan: use/mcp
sidebar: { label: "Connect an AI Assistant With MCP" }
---

The Model Context Protocol (MCP) exposes Lyra’s metric catalog and job workflow to an AI assistant. Connect your MCP client to the operator’s configured endpoint, such as `https://lyra.example.com/mcp/`, with the agent Bearer credential.

The transport uses the agent key and exposes consumer operations. The [tool reference](../../reference/generated/mcp/) gives exact contracts.

The examples use the finished [city plugin](../../plugins/tutorial/package/). Your operator must [enable it on the server](../../operate/plugins/). Its bundled dataset requires no resource settings or data-service credentials.

## 1. Discover a suitable metric

Search with `lyra_search_metrics`, or use `lyra_list_metrics` for an inventory. Inspect the selected metric with `lyra_get_metric` before choosing inputs.

List and search group public editions under one metric family. Each entry describes what the metric measures, its output granularity, and the source types each edition accepts.

Call `lyra_get_metric` with `metric` to inspect the family’s shared input, output, and parameter schemas. For named editions, also pass `edition` to inspect the exact source bindings and accepted selections. Select a named edition even when only one is available. For a family without named editions, omit `edition` to inspect its executable contract.

## 2. Submit once

Call `lyra_run_metric` with `metric` and `input`. Use the input names and accepted source types returned by `lyra_get_metric` to build `input`. Always include `input`; use `{}` for a metric with no inputs.

For `city_street_maintenance`, submit the ID selection and metric parameters together. Keep the example’s `example:city` and `street` values, and supply IDs known to its provider:

```json
{
  "metric": "city_street_maintenance",
  "input": {
    "streets": {
      "collection": "example:city",
      "unit_type": "street",
      "ids": ["street-1"]
    },
    "parameters": {"condition_threshold": 50}
  }
}
```

Put metric parameters under `input.parameters`, following the `parameters` schema returned by `lyra_get_metric`. Omit this object when the metric declares no parameters.

For each named location input, supply a [collection, unit type, and nonempty ID list](../spatial-inputs/). Workers report unknown IDs and provider failures during execution, identifying the affected node and input.

The submission response gives you a job ID and result reference:

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "next_tool": "lyra_get_job_result"
}
```

Lyra rejects invalid editions, parameters, and source selections before accepting the job. Workers resolve locations during execution. Every call creates an independent job. A timeout may happen after acceptance, so another submission can duplicate the calculation.

## 3. Observe the result

Call `lyra_get_job_result` with only `result_ref`. For `queued` or `running`, wait two seconds before the next inspection.

The tool call succeeds (`isError: false`) even when it reports a failed job. Check the job’s status and error before deciding what to do next.

Unknown IDs fail the job during execution. The job also fails if a provider cannot supply the data revision captured when Lyra accepted it. A conversion that returns no locations produces a valid empty selection, and execution continues.

Successful inspection groups declarations, column summaries, and previews by named output. All outputs share a preview limit of five rows and twenty columns. Truncation metadata reports what the response omits or shortens. Column summaries include numeric bounds only when all non-null values are numeric.

For the request above, `maintenance` contains one row for `street-1`. Its `length_m` and `maintenance_length_m` both equal 100. You can also submit the [tutorial's block-selection handoff request](../../plugins/tutorial/package/#hand-off-a-known-request); it returns three street rows through the same metric.

Lyra limits the complete MCP response to 64 KiB, counting its text and structured copies. It may omit detailed metadata, previews, provenance, or errors to fit. Mandatory identifiers and output names remain; if those alone exceed the limit, the tool returns `result_response_too_large`.

## 4. Download the complete result

Call `lyra_download_result` with only `result_ref`. Fetch `lyra_api.url` with the agent Bearer credential to download the JSON result.

The download contains every named output of the requested metric, even if Lyra truncated the previews. Intermediate dependency tables stay internal. Fetch `descriptor.url` from the inspection response for full output declarations and request provenance.

Use the result’s metric and edition fields to identify the requested calculation. Compact [provenance](../results/#inspect-execution-provenance) includes `cache_revision`, the optional value that controls cache reuse for the whole run. Fetch the descriptor to inspect the accepted execution plan and what happened during this job, including cache reuse. The descriptor records the plan separately from observed execution.

Inspection summarizes each submitted ID selection as an object with a field count. Fetch the descriptor to read the collection, unit type, and IDs. Unknown metadata is null, and spatial preview coordinates use World Geodetic System 1984 (WGS84).

## Handle unavailable results

An unavailable result can mean that work is still active, retained data has expired, or execution failed. Use the error code to distinguish these cases:

| Error | Meaning |
| --- | --- |
| `result_not_found` | The reference is unknown or expired |
| `result_unavailable` | The job succeeded, but its retained payload is missing |
| `result_not_ready` | A download was requested while work was active |
| `result_not_downloadable` | The job failed |

Active jobs have no terminal retention deadline. Download completed data before its reported lifetime ends. The current job status determines whether Lyra reports an active or completed job.

Infrastructure and deadline failures can be retryable tool errors. Observation retries are bounded; they do not trigger resubmission. Decide whether a new execution is needed when retained data is gone.

See [payload examples](../../reference/mcp-payloads/) for complete formatted responses and [results](../results/) for table interpretation.

## Inspect calculation relationships

Use `lyra_get_metric_dependencies` to inspect a metric’s direct dependencies and consumers. Pass the metric family as `metric` and include `edition` for families with named editions. Reserve this lookup for questions about calculation structure; routine execution and result interpretation are self-contained.

Each relationship identifies:

- The producer and consumer nodes, including their families and editions
- Whether either node is private
- The producer’s named output, or `null` for a location operation
- The receiving input name or location binding path

The tool returns at most 50 relationships in a consistent order and reports how many it omits in `omitted_relationships`. It limits node descriptions to 240 characters. Relationships include declared location operations; available selection routes do not add dependencies.
