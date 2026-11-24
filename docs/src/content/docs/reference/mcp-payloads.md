---
title: Inspect MCP request and response payloads
description: Inspect representative tool requests, job responses, and durable retrieval payloads.
contentType: Reference
contentPlan: reference/mcp-payloads
sidebar: { label: "MCP Payload Examples" }
---

These Model Context Protocol (MCP) examples show JSON requests and the values tools return in `structuredContent`. For job, description, and report inspections, read `structuredContent.result`. If you read the text response instead, job inspections include the `result` wrapper. Description and report inspections contain the value inside that wrapper.

Responses can include optional `null` fields. Argument validation errors set `isError` to `true` and describe the failure in text content.

For the procedure, read [the MCP workflow](../../use/mcp/). For exact schemas, use [the tool reference](../generated/mcp/).

## Submission input

Pass the registered metric name in `metric`. Use `lyra_get_metric` to find the accepted selections for `inputs` and the schema for `parameters`. Include `interventions` when you want to change a catalog for this run.

For the city metric, select `city_streets` with IDs known to that provider. For another metric, use its inspected accepted selections. This request selects one street for the city maintenance metric:

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

## Submit an inline street proposal

Pass this JSON to `lyra_run_metric` or send it as the body of `POST /jobs`. For this run, `remove` excludes `street-1` and `add` defines its proposed replacement. The `streets` input selects the new ID, so the metric measures the proposed 150 m street.

```json {4,6-16}
{
  "metric": "city_street_lengths",
  "inputs": {
    "streets": {"provider": "city_streets", "ids": ["proposed-1"]}
  },
  "interventions": [{
    "catalog": "city_streets",
    "id": "street-extension",
    "revision": "1",
    "add": [{
      "location_id": "proposed-1",
      "geometry": "LINESTRING (500050 2100000, 500050 2100150)",
      "condition_score": 20.0
    }],
    "remove": ["street-1"]
  }]
}
```

Include every declared column in each addition. Use the provider’s schema to choose the JSON representation for each value.

| Provider value | JSON representation |
| --- | --- |
| String or location ID | String with the original spelling, including leading zeros |
| Signed 64-bit integer | Decimal string, such as `"42"` |
| Finite float | JSON number |
| Boolean | `true` or `false` |
| Geometry | Full-precision WKT in the provider’s CRS |
| Missing value in a nullable column | `null` |

For additions alone, omit `remove` or send `[]`. For removals alone, omit `add` or send `[]`. Lyra rejects unknown request or intervention fields and explicit `null` for sections or actions. See [intervention validation rules](../../use/spatial-inputs/#prepare-changes-for-a-catalog).

## Submission response

Keep the job ID to check the job’s status and retrieve its result.

```json
{
  "job_id": "12345678901234567890123456789012",
  "next_tool": "lyra_get_job_result"
}
```

## Queued job

Wait for `poll_after_seconds` before checking status again.

```json
{
  "result": {
    "job_id": "12345678901234567890123456789012",
    "status": "queued",
    "metric": "city_street_maintenance",
    "created_at": "2026-09-24T12:00:00Z",
    "updated_at": "2026-09-24T12:00:00Z",
    "poll_after_seconds": 2,
    "truncation": {
      "omitted_rows": 0,
      "omitted_columns": 0,
      "shortened_strings": 0,
      "omitted_sections": []
    }
  }
}
```

## Running job

A running job may report progress, but lifecycle status determines completion. This response includes both:

```json
{
  "result": {
    "job_id": "12345678901234567890123456789012",
    "status": "running",
    "metric": "city_street_maintenance",
    "created_at": "2026-09-24T12:00:00Z",
    "updated_at": "2026-09-24T12:00:03Z",
    "started_at": "2026-09-24T12:00:01Z",
    "progress": {
      "timestamp": "2026-09-24T12:00:03Z",
      "stage": "compute",
      "current": 1,
      "total": 10
    },
    "poll_after_seconds": 2,
    "truncation": {
      "omitted_rows": 0,
      "omitted_columns": 0,
      "shortened_strings": 0,
      "omitted_sections": []
    }
  }
}
```

## Successful table result

A successful response groups output descriptions and sample rows by output name. Truncation fields report what the response omits. This excerpt shows the 100 m maintenance workload for `street-1`:

```json
{"outputs": {"maintenance": {"preview": [{"maintenance_length_m": 100.0}]}}}
```

Download the [complete table result response](../../examples/mcp/table-result.json) to inspect all fields.

## Failed job

The tool call succeeds (`isError: false`) even when it reports a failed job.

```json
{
  "result": {
    "job_id": "12345678901234567890123456789014",
    "status": "failed",
    "result_kind": "failed",
    "lifetime": {},
    "download": "/jobs/12345678901234567890123456789014/result/download",
    "error": {
      "message": "Execution failed"
    },
    "truncation": {
      "omitted_rows": 0,
      "omitted_columns": 0,
      "shortened_strings": 0,
      "omitted_sections": []
    }
  }
}
```

## Unknown or expired job

A tool error reports `result_not_found` with `isError: true`.

```json
{
  "error": {
    "code": "result_not_found",
    "message": "This job is unknown or expired.",
    "details": {
      "job_id": "12345678901234567890123456789015"
    }
  }
}
```

If required stored data is missing, corrupt, or inconsistent, inspection returns `result_observation_error`. Repeating the lookup cannot repair that data.

## Download handoff

For a successful job, the download handoff returns a URL and authentication instructions. Send the agent Bearer credential when fetching that URL. For an active job, the tool returns `result_not_ready`; for a failed job, it returns `result_not_downloadable`.

```json
{
  "job_id": "12345678901234567890123456789012",
  "status": "succeeded",
  "format": "json",
  "media_type": "application/json",
  "lifetime": {
    "expires_in_seconds": 3600
  },
  "lyra_api": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/12345678901234567890123456789012/result/download",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  }
}
```

## Multiple named outputs

This example returns a summary table and a spatial parcel table. Each output has its own metadata and preview:

```json
{
  "outputs": {
    "summary": {"preview": [{"count": 1}]},
    "parcels": {
      "preview": {
        "type": "FeatureCollection",
        "features": [{
          "type": "Feature",
          "geometry": {"type": "Point", "coordinates": [-99, 20]},
          "properties": {"count": 2}
        }]
      }
    }
  }
}
```

Download the [complete multiple outputs response](../../examples/mcp/multiple-outputs.json) to inspect all fields.

## Compact provenance

[Provenance](../../concepts/provenance/) records the submitted inputs and plugin details. In this inspection excerpt, `count: 2` reports the number of fields in the submitted selection. It does not count selected locations. Download the full result to read those fields:

- `provider`
- `ids`

The compact inspection represents the selection as follows:

```json
{
  "provenance": {
    "input": {
      "location": {
        "type": "object",
        "count": 2
      }
    }
  }
}
```

Download the [complete compact provenance response](../../examples/mcp/compact-provenance.json) to inspect all fields.

## Retained description preview

To inspect saved metadata, pass your job ID to `lyra_describe_result` in a `tools/call` request.

```json
{
  "name": "lyra_describe_result",
  "arguments": {
    "job_id": "12345678901234567890123456789012"
  }
}
```

When the complete description exceeds the inspection limit, the tool returns a preview:

```json
{
  "result": {
    "kind": "preview",
    "job_id": "12345678901234567890123456789012",
    "omitted": [
      "description"
    ],
    "download_url": "https://lyra.example/api/results/12345678901234567890123456789012"
  }
}
```

Follow the link with your agent Bearer credential for the complete description, including every output schema and provider scope. When it fits inline, the inspection has `kind="complete"` and the complete `description`.

## Retained report preview

Use the same job ID with `lyra_inspect_report` to inspect the execution report.

```json
{
  "name": "lyra_inspect_report",
  "arguments": {
    "job_id": "12345678901234567890123456789012"
  }
}
```

Large parameters and large graphs produce the same fixed preview:

```json
{
  "result": {
    "kind": "preview",
    "job_id": "12345678901234567890123456789012",
    "metric": "city_street_maintenance",
    "status": "succeeded",
    "omitted": [
      "report"
    ],
    "download_url": "https://lyra.example/api/executions/12345678901234567890123456789012/report/download"
  }
}
```

The authenticated download returns the complete report without an inspection wrapper. A fitting inspection has `kind="complete"` and the entire `report`.

## Retained Arrow download

Choose a named output from the description:

```json
{
  "name": "lyra_download_table",
  "arguments": {
    "job_id": "12345678901234567890123456789012",
    "output_name": "maintenance"
  }
}
```

The tool returns a link, with no Arrow data in the MCP envelope:

```json
{
  "job_id": "12345678901234567890123456789012",
  "output_name": "maintenance",
  "download_url": "https://lyra.example/api/results/12345678901234567890123456789012/tables/download?output_name=maintenance"
}
```

Fetch it with your agent Bearer credential. The content type is `application/vnd.apache.arrow.stream`; use ordinary PyArrow or GeoPandas readers for the saved stream.

## Retained location inputs

Choose an available `input_name` from the result description and request its Arrow download link.

```json {2,4-5}
{
  "name": "lyra_download_location_input",
  "arguments": {
    "job_id": "12345678901234567890123456789012",
    "input_name": "streets"
  }
}
```

The response includes these fields in `structuredContent`.

| Field | Value |
| --- | --- |
| `job_id` | The requested job ID |
| `input_name` | The requested location-input name |
| `download_url` | An absolute Arrow download URL using the deployment’s public API base URL |

Fetch `download_url` with your agent Bearer credential. The URL uses the [HTTP input-download route](../../use/rest-api/#read-retained-location-inputs), including any configured public path prefix.

For an unavailable input, the tool sets `isError` to `true` and returns this payload in `structuredContent`:

```json {3,5-6}
{
  "error": {
    "code": "location_input_unavailable",
    "message": "The location input was not materialized for this result.",
    "job_id": "12345678901234567890123456789012",
    "input_name": "streets"
  }
}
```

For `result_not_found` and `result_integrity_error`, the error includes the requested input name and `output_name=null`.
