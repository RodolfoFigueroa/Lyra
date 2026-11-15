---
title: Connect an AI assistant with MCP
description: Discover a metric, submit once, check job status, and download the complete result.
contentType: How-to
contentPlan: use/mcp
sidebar: { label: "Connect An AI Assistant With MCP" }
---

Connect an AI assistant to Lyra through the Model Context Protocol (MCP). You can then discover metrics, submit jobs, and retrieve their results. Connect your MCP client to the operator’s endpoint, such as `https://lyra.example.com/mcp/`, with the agent Bearer credential.

Connect to MCP through your existing Lyra server using the agent key. The [tool reference](../../reference/generated/mcp/) lists the available tools and their request and response schemas.

The examples use the finished [city plugin](../../plugins/tutorial/package/). Your operator must [enable it on the server](../../operate/plugins/). Its bundled dataset requires no resource settings or data-service credentials.

## 1. Discover a suitable metric

Search with `lyra_search_metrics`, or use `lyra_list_metrics` for an inventory. Inspect the selected metric with `lyra_get_metric` before choosing inputs.

List and search results identify public metrics by their registered names. Each entry describes the calculation, its output rows, and accepted source types.

Pass the selected name as `metric` to `lyra_get_metric`. Read these fields before submitting:

- `parameters`: parameter constraints and defaults
- `outputs`: named result schemas
- `accepted_selections`: source types you can select for each input
- `bindings`: concrete source contracts for native inputs
- `computed_inputs`: inputs that dependencies supply during execution

Build your request from the accepted selections and parameter schema. Lyra supplies computed inputs; don’t include them in the request.

## 2. Submit once

Call `lyra_run_metric` with `metric`, `inputs`, and `parameters`. Build `inputs` from the names and accepted source types that `lyra_get_metric` returns.

Lyra uses an empty object when you omit `inputs` or `parameters`. Supply any selections and parameter values the metric requires. Lyra rejects `null` for `metric`, `inputs`, or `parameters`.

For `city_street_maintenance`, submit the ID selection and metric parameters together. Use the `city_streets` provider and IDs from its dataset:

```json {3-10}
{
  "metric": "city_street_maintenance",
  "inputs": {
    "streets": {
      "provider": "city_streets",
      "ids": ["street-1"]
    }
  },
  "parameters": {"condition_threshold": 50}
}
```

Set calculation values in `parameters`, following the schema from `lyra_get_metric`. Omit this object when the metric declares no parameters.

For each named location input, supply a [provider ID and list of record IDs](../spatial-inputs/). Workers report unknown IDs and provider failures during execution, identifying the affected node and input.

The submission response gives you a job ID:

```json
{
  "job_id": "job-1",
  "next_tool": "lyra_get_job_result"
}
```

Lyra rejects unknown metric names, invalid parameters, and invalid source selections before accepting a job. Workers resolve locations during execution.

Every submission creates an independent job. A timeout can happen after acceptance, so resubmitting can duplicate the calculation.

## 3. Observe the result

Pass the returned `job_id` unchanged as a nonempty string. Lyra preserves whitespace. Empty strings and other types produce validation errors with `isError: true` and an explanation in text content.

Call `lyra_get_job_result` with only `job_id`. Read the job’s status and output previews from `structuredContent.result`. If `status` is `queued` or `running`, wait two seconds before the next inspection.

The tool call succeeds (`isError: false`) even when it reports a failed job. Check the job’s status and error before deciding what to do next.

Unknown IDs fail the job during execution. The job also fails if a provider cannot supply the data revision captured when Lyra accepted it. A conversion that returns no locations produces a valid empty selection, and execution continues.

For successful jobs, read `execution_id` to identify the saved report. Inspection groups declarations, column summaries, and previews by output name. All outputs share a preview limit of five rows and twenty columns.

Truncation metadata records what the response omits or shortens. Column summaries include numeric bounds only when all non-null values are numeric. These limits don’t truncate the saved report; [read the complete report](../results/#reopen-a-local-result) through `ResultStore` with access to the server’s store.

For the request above, `maintenance` contains one row for `street-1`. Its `length_m` and `maintenance_length_m` both equal 100. You can also submit the [tutorial’s block-selection handoff request](../../plugins/tutorial/package/#hand-off-a-known-request); it returns three street rows through the same metric.

Lyra limits the complete MCP response to 64 KiB (65,536 bytes), counting its text and structured copies. It may omit detailed metadata, previews, provenance, or errors to fit. Mandatory identifiers and output names remain; if those alone exceed the limit, the tool returns `result_response_too_large`.

## 4. Download the complete result

Call `lyra_download_result` with only `job_id`. Read the download instructions from `structuredContent`. Fetch `structuredContent.lyra_api.url` with the agent Bearer credential to download the JSON result.

The download contains every named output of the requested metric, even if Lyra truncated the previews. Intermediate dependency tables stay internal. The inspection’s `download` field is a relative path to the complete result. Use `lyra_download_result` for an absolute URL and authentication instructions.

Use the result’s `metric` field to identify the requested calculation. Inspect the downloaded result’s [provenance](../results/#inspect-execution-provenance) to compare the accepted plan with execution observations, including cache reuse.

Inspection summarizes each submitted ID selection as an object with a field count. Download the full result to read the provider and record IDs. Unknown metadata is null, and spatial preview coordinates use WGS84.

## Handle unavailable results

An unavailable result can mean that work is still active, retained data has expired, or execution failed. Use the error code to distinguish these cases:

| Error | Meaning |
| --- | --- |
| `result_not_found` | The job ID is unknown or expired |
| `result_observation_error` | Required retained job data is missing, corrupt, or inconsistent |
| `result_not_ready` | You requested a download while the job was active |
| `result_not_downloadable` | The job failed |

Active jobs have no terminal retention deadline. Download completed data before its reported lifetime ends. The current job status determines whether Lyra reports an active or completed job.

Infrastructure and deadline failures can produce retryable tool errors. Retry the observation without resubmitting the job. If retained data is gone, decide whether you need a new execution.

See [payload examples](../../reference/mcp-payloads/) for complete formatted responses and [results](../results/) for table interpretation.

## Inspect calculation relationships

Use `lyra_get_metric_dependencies` to inspect a metric’s direct dependencies and consumers. Pass the registered metric name as `metric`. Use this lookup to understand calculation structure. You don’t need it for routine execution or result interpretation.

Each relationship identifies:

- The producer and consumer nodes, including their registered names and node kinds
- Whether either node is private
- The producer’s named output, or `null` for a location operation
- The receiving input name or location binding path

The tool returns at most 50 relationships in a consistent order, with an omission count in `omitted_relationships`. Node descriptions have a 240-character limit. Relationships include declared location operations; available selection routes do not add dependencies.
