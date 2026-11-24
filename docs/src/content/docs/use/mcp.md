---
title: Connect an AI assistant with MCP
description: Discover a metric, submit once, check job status, and download the complete result.
contentType: How-to
contentPlan: use/mcp
sidebar: { label: "Connect An AI Assistant With MCP" }
---

Connect an AI assistant to Lyra through the Model Context Protocol (MCP). You can then discover metrics, submit jobs, and retrieve their results. Connect your MCP client to the operator’s endpoint, such as `https://lyra.example.com/mcp/`, with the agent Bearer credential.

See the [MCP tool reference](../../reference/generated/mcp/) for request and response schemas.

The examples use the finished [city plugin](../../plugins/tutorial/package/). Your operator must [enable it on the server](../../operate/plugins/). Its bundled dataset requires no resource settings or data-service credentials.

## 1. Discover a suitable metric

Search with `lyra_search_metrics`, or use `lyra_list_metrics` for an inventory. Inspect the selected metric with `lyra_get_metric` before choosing inputs.

For an inventory, start at `offset=0` (the default) and choose a `limit` from 1 to 20. Pass each `next_offset` as the next call’s `offset` until `next_offset` is `null`. An offset at or above `total_count` returns an empty page. If `catalog_fingerprint` changes between calls, restart at zero to list metrics from a single catalog version.

Both tools return each public metric’s registered name. Check its `accepted_selections` for the source provider IDs you can use for each input.

Pass the registered name as `metric` to `lyra_get_metric`. Use the response to choose inputs and settings for your request.

| Field | What to check |
| --- | --- |
| `metric.accepted_selections` | Source provider IDs you can select for each input you supply |
| `parameters` | Allowed settings and defaults |
| `location_inputs` | Target provider for each input. For a derived input, read `operation` and `source_provider` to see how Lyra obtains its records. |
| `computed_inputs` | Inputs Lyra supplies through dependencies |
| `providers` | Record schemas and capabilities by provider ID |
| `outputs` | Result tables and their schemas |

To add or remove records for a run, find your provider in `providers` and check that `membership_capable` is `true`. For additions, read `record_schema.columns` for each column’s type, unit, and nullability. Prepare coordinates in the provider’s coordinate reference system, `crs`.

## 2. Submit once

Call `lyra_run_metric` with the metric name in `metric`. Supply the `inputs` and `parameters` that its contract requires. Lyra uses an empty object for either field if you omit it.

For a proposal, send complete addition records and removal IDs in `interventions`. For a baseline, omit `interventions` or send `[]`. Lyra rejects explicit `null` for these request fields. The [street proposal payload](../../reference/mcp-payloads/#submit-an-inline-street-proposal) shows the JSON for a replacement street.

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
  "job_id": "12345678901234567890123456789012",
  "next_tool": "lyra_get_job_result"
}
```

Lyra rejects unknown metric names, invalid parameters, and invalid source selections before accepting a job. Workers resolve locations during execution.

Every submission creates an independent job. A timeout can happen after acceptance, so resubmitting can duplicate the calculation.

## 3. Observe the result

Copy the `job_id` from the submission response. It contains 32 lowercase hexadecimal characters. If an argument is invalid, the tool sets `isError` to `true` and returns the validation error in its text content.

Call `lyra_get_job_result` with only `job_id`. Read the job’s status and output previews from `structuredContent.result`. If `status` is `queued` or `running`, wait two seconds before the next inspection.

The tool call succeeds (`isError: false`) even when it reports a failed job. Check the job’s status and error before deciding what to do next.

Unknown IDs fail the job during execution. The job also fails if a provider cannot supply the data revision captured when Lyra accepted it. A conversion that returns no locations produces a valid empty selection, and execution continues.

Inspection groups declarations, column summaries, and previews by output name. Across all outputs, the preview includes up to five rows and twenty columns. Use the same `job_id` to retrieve saved tables and the report.

Submit the baseline and the proposal as separate jobs. For each job ID, inspect the submitted changes with `lyra_inspect_report`. Then compare the [output tables](#download-a-retained-table) and [selected input records](#read-retained-location-inputs).

Truncation metadata records what the response omits or shortens. Column summaries include numeric bounds only when all non-null values are numeric. To read the saved report without truncation, [inspect it with its job ID](#inspect-a-retained-execution-report).

For the request above, `maintenance` contains one row for `street-1`. Its `length_m` and `maintenance_length_m` both equal 100. You can also submit the [tutorial’s block-selection handoff request](../../plugins/tutorial/package/#hand-off-a-known-request); it returns three street rows through the same metric.

Lyra limits the complete MCP response to 65,536 bytes, counting its text and structured copies. It may omit detailed metadata, previews, provenance, or errors to fit. Mandatory identifiers and output names remain; if those alone exceed the limit, the tool returns `result_response_too_large`.

## 4. Download the complete result

Call `lyra_download_result` with only `job_id`. Read the download instructions from `structuredContent`. Fetch `structuredContent.lyra_api.url` with the agent Bearer credential to download the JSON result.

The download contains every named output of the requested metric, even if Lyra truncated the previews. Intermediate dependency tables stay internal. The inspection’s `download` field is a relative path to the complete result. Use `lyra_download_result` for an absolute URL and authentication instructions.

Use the result’s `metric` field to identify the requested calculation. Inspect the downloaded result’s [provenance](../results/#inspect-execution-provenance) to compare the accepted request with execution observations, including cache reuse.

Inspection summarizes each submitted ID selection as an object with a field count. Download the full result to read the provider and record IDs. Unknown metadata is null, and spatial preview coordinates use WGS84.

## Describe a retained result

Pass your job ID to `lyra_describe_result` to read the saved metadata without loading tables or the report. The metadata remains available after queue expiry or plugin changes while the store retains the result.

Read `structuredContent.result.kind` to choose your next step:

- `complete`: read the [result description](../results/#read-a-retained-description) from `structuredContent.result.description`
- `preview`: fetch `structuredContent.result.download_url` with your agent Bearer credential

A preview identifies the saved metadata with `job_id`. Its `omitted` field is `["description"]`, and its absolute `download_url` preserves your deployment’s public path prefix.

Lyra returns a preview when the complete response exceeds 65,536 bytes. This limit counts the SDK envelope and both text and structured representations. The preview omits the entire description; Lyra never trims individual schemas, parameters, or outputs.

The authenticated download returns complete `application/json` without an inspection wrapper or inspection size limit. Use your agent Bearer credential; Lyra rejects requests without it.

Handle description lookup failures by their error code:

| Code | Meaning |
| --- | --- |
| `result_not_found` | No result exists for this job ID |
| `result_integrity_error` | Required metadata or relationships are damaged |

Each lookup error includes `job_id` and `output_name=null`. Missing or wrongly typed tool arguments produce SDK validation errors instead.

## Inspect a retained execution report

Pass your job ID to `lyra_inspect_report` to read the [execution report](../../concepts/provenance/). The tool reads the saved report without loading tables or running the calculation again.

Read `structuredContent.result.kind` to choose your next step:

- `complete`: read the entire report from `structuredContent.result.report`
- `preview`: fetch `structuredContent.result.download_url` with your agent Bearer credential

A report preview includes the job ID, root metric, and `status="succeeded"`. When `omitted` contains `"report"`, fetch `download_url` for the complete report as `application/json`. The URL includes your deployment’s public path prefix.

Lyra returns a preview when the complete response exceeds 65,536 bytes. This limit includes the SDK envelope and both text and structured copies of the report. Large parameters and large graphs produce the same preview; the download preserves the complete report without an inspection size limit.

If lookup fails, read `structuredContent.error` when `isError` is `true`. Each error includes `code`, `message`, and `job_id`:

| Code | Meaning |
| --- | --- |
| `report_not_found` | No retained report matches the job ID |
| `report_integrity_error` | The retained report or its required relationships are damaged |

Missing or wrongly typed arguments produce SDK validation errors instead. For other clients, follow [report retrieval through HTTP and Python](../results/#read-an-execution-report).

## Download a retained table

Choose an `output_name` from the [result description](#describe-a-retained-result). Pass it with your job ID to `lyra_download_table` to get the Arrow download URL.

```json
{
  "job_id": "12345678901234567890123456789012",
  "output_name": "maintenance"
}
```

Read these fields directly from `structuredContent`:

- `job_id`: identifies the saved result
- `output_name`: identifies the selected table
- `download_url`: gives the complete download address, including your deployment’s public path prefix

Fetch `download_url` with your agent Bearer credential and save the response bytes unchanged. Read the saved Arrow stream with [PyArrow or GeoPandas](../rest-api/#download-a-retained-table), regardless of filename. You can download the table after job expiry while Lyra retains the result.

The tool checks that the output exists. MCP limits the link response to 65,536 bytes, including both response representations and the envelope.

When you fetch the URL, Lyra checks the selected partitions, row counts, and geometry before sending the Arrow stream. Missing rows or malformed geometry return HTTP `500` with `result_integrity_error`.

If `isError` is `true`, inspect `structuredContent.error.code`:

| Code | Meaning |
| --- | --- |
| `result_not_found` | The result or output name doesn’t exist |
| `result_integrity_error` | Required stored data is missing or damaged |

Each lookup error includes `job_id` and the requested `output_name`. Missing or wrongly typed arguments produce SDK validation errors instead.

## Handle unavailable job results

An unavailable result can mean that work is still active, retained data has expired, or execution failed. Use the error code to distinguish these cases:

| Error | Meaning |
| --- | --- |
| `result_not_found` | The job ID is unknown or expired |
| `result_observation_error` | Required retained job data is missing, corrupt, or inconsistent |
| `result_not_ready` | You requested a download while the job was active |
| `result_not_downloadable` | The job failed |

Save `job_id` when submitting the job. Use these tools after job expiry:

| Task | Tool |
| --- | --- |
| Read saved metadata | `lyra_describe_result` |
| Inspect the report | `lyra_inspect_report` |
| Get an Arrow download URL | `lyra_download_table` |

Active jobs have no terminal retention deadline. Use the current job status to distinguish active and completed jobs.

Infrastructure and deadline failures can produce retryable tool errors. Retry the observation without resubmitting the job. If retained data is gone, decide whether you need a new execution.

## Inspect calculation relationships

Use `lyra_get_metric_dependencies` to inspect a metric’s direct dependencies and consumers. Pass the registered metric name as `metric`. Use this lookup to understand calculation structure. You don’t need it for routine execution or result interpretation.

Each relationship identifies:

- The producer and consumer nodes, including their registered names and node kinds
- Whether either node is private
- The producer’s named output, or `null` for a location operation
- The receiving input name or location binding path

The tool returns at most 50 relationships in a consistent order, with an omission count in `omitted_relationships`. Node descriptions have a 240-character limit. Relationships include declared location operations; available selection routes do not add dependencies.

## Read retained location inputs

Use your saved `job_id` to retrieve location inputs through MCP. Choose `input_name` from `location_inputs` in the [retained result description](#describe-a-retained-result). Each entry describes the input’s schema, dataset scope, and availability, including empty and unavailable selections.

If the description exceeds the response limit, follow the preview’s authenticated JSON download URL. The download includes every output and input description without truncating schemas.

Call `lyra_download_location_input` with `job_id` and `input_name`. If the input is available, the tool returns a download URL. Read these fields directly from `structuredContent`:

- `job_id`: the saved result
- `input_name`: the selected location input
- `download_url`: the absolute URL, including your deployment’s public path prefix

Fetch `download_url` with your agent Bearer credential. If a required record is missing or its geometry is malformed, Lyra returns HTTP `500` with `result_integrity_error` before sending the Arrow stream. The stream includes the declared schema and CRS even for empty selections.

When `isError` is `true`, inspect `structuredContent.error` using the [HTTP error codes and fields](../rest-api/#read-retained-location-inputs). Handle input-specific failures as follows:

- `location_input_unavailable`: retrying doesn’t materialize the input or change its availability
- `result_not_found`: check the job ID and choose an input name from `description.location_inputs`

Missing or wrongly typed tool arguments produce SDK validation errors in text content. See [location-input payload examples](../../reference/mcp-payloads/#retained-location-inputs) for request and response fields.
