---
title: Inspect MCP request and response payloads
description: Inspect representative tool requests, job responses, and durable retrieval payloads.
contentType: Reference
contentPlan: reference/mcp-payloads
sidebar: { label: "MCP Payload Examples" }
---

These Model Context Protocol (MCP) examples show JSON tool requests and responses. Response examples show `structuredContent`, with job, description, and report inspections under `structuredContent.result`. For those inspections, the text content represents the inner result; other tools represent the direct structured payload.

Responses can include optional `null` fields. Argument validation errors set `isError` to `true` and describe the failure in text content.

For the procedure, read [the MCP workflow](../../use/mcp/). For exact schemas, use [the tool reference](../generated/mcp/).

## Submission input

Submission accepts `metric`, `inputs`, and `parameters`. Before submitting, use `lyra_get_metric` to inspect the selected metric’s contract:

- Input names
- Accepted source types
- Parameter schema

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

## Submission response

Keep the job ID to check the job’s status and retrieve its result.

```json
{
  "job_id": "job-1",
  "next_tool": "lyra_get_job_result"
}
```

## Queued job

Wait for `poll_after_seconds` before checking status again.

```json
{
  "result": {
    "job_id": "job-1",
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
    "job_id": "job-1",
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
    "job_id": "failed-1",
    "status": "failed",
    "result_kind": "failed",
    "lifetime": {},
    "download": "/jobs/failed-1/result/download",
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
      "job_id": "old-1"
    }
  }
}
```

If required stored data is missing, corrupt, or inconsistent, inspection returns `result_observation_error`. Repeating the lookup cannot repair that data.

## Download handoff

For a successful job, the download handoff returns a URL and authentication instructions. Send the agent Bearer credential when fetching that URL. For an active job, the tool returns `result_not_ready`; for a failed job, it returns `result_not_downloadable`.

```json
{
  "job_id": "job-1",
  "status": "succeeded",
  "format": "json",
  "media_type": "application/json",
  "lifetime": {
    "expires_in_seconds": 3600
  },
  "lyra_api": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/job-1/result/download",
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

Pass these `tools/call` parameters with a saved result reference. Use your saved reference in place of the example value:

```json
{
  "name": "lyra_describe_result",
  "arguments": {
    "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
```

When the complete description exceeds the inspection limit, the tool returns a preview:

```json
{
  "result": {
    "kind": "preview",
    "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "execution_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "omitted": [
      "description"
    ],
    "download_url": "https://lyra.example/api/results/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/description/download"
  }
}
```

Follow the link with your agent Bearer credential for the complete description, including every output schema and provider scope. When it fits inline, the inspection has `kind="complete"` and the complete `description`.

## Retained report preview

Use the saved execution ID:

```json
{
  "name": "lyra_inspect_report",
  "arguments": {
    "execution_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}
```

Large parameters and large graphs produce the same fixed preview:

```json
{
  "result": {
    "kind": "preview",
    "execution_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "metric": "city_street_maintenance",
    "status": "succeeded",
    "omitted": [
      "report"
    ],
    "download_url": "https://lyra.example/api/executions/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/report/download"
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
    "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "output_name": "maintenance"
  }
}
```

The tool returns a link, with no Arrow data in the MCP envelope:

```json
{
  "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "output_name": "maintenance",
  "download_url": "https://lyra.example/api/results/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/tables/maintenance/download"
}
```

Fetch it with your agent Bearer credential. The content type is `application/vnd.apache.arrow.stream`; use ordinary PyArrow or GeoPandas readers for the saved stream.

## Retained row pages

To request the first page, omit `cursor`:

```json
{
  "name": "lyra_get_result_rows",
  "arguments": {
    "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "output_name": "maintenance"
  }
}
```

The last page sets `next_cursor` to `null`:

```json
{
  "rows": [
    {
      "location_id": "street-1",
      "row_key": "street-1",
      "length_m": 100.0,
      "maintenance_length_m": 100.0
    }
  ],
  "next_cursor": null
}
```

For a continuing page, pass its opaque `next_cursor` unchanged with the same result and output. Every declared column is present, including null cells. Integers are decimal strings even when small; geometry uses well-known text (WKT) in the saved coordinate reference system (CRS).

An oversized row produces `isError: true` with this structured error. The request cursor remains at that row:

```json
{
  "error": {
    "code": "row_too_large",
    "message": "The next row exceeds the response size limit.",
    "result_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "output_name": "maintenance",
    "download_url": "https://lyra.example/api/results/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/tables/maintenance/download"
  }
}
```

Download the complete Arrow table with the same credential. It includes rows already returned in previous pages. Missing or wrongly typed arguments instead produce SDK validation errors in text content.

## Retained location inputs

Location-input tools return saved rows or authenticated Arrow download URLs for a completed result. Choose `input_name` from the result description’s `location_inputs`.

This request reads the first page using the default limit of 100 rows. Replace `result_ref` with your saved reference:

```json {2,4-5}
{
  "name": "lyra_get_location_input_rows",
  "arguments": {
    "result_ref": "12345678901234567890123456789012",
    "input_name": "streets"
  }
}
```

The response includes `rows` and `next_cursor` directly in `structuredContent`. Input rows contain provider attributes and geometry, with no metric `row_key`. Correspondence rows map immediate source records to target records through `source_location_id` and `location_id`.

`lyra_download_location_input` accepts the same `result_ref` and `input_name` without pagination arguments. Its response contains these fields directly in `structuredContent`:

| Field | Value |
| --- | --- |
| `result_ref` | The requested result reference |
| `input_name` | The requested location-input name |
| `download_url` | An absolute Arrow download URL using the deployment’s public API base URL |

Fetch `download_url` with your agent Bearer credential. The URL uses the [HTTP input-download route](../../use/rest-api/#read-retained-location-inputs), including any configured public path prefix.

For an unavailable input, the tool sets `isError` to `true` and returns this payload in `structuredContent`:

```json {3,5-6}
{
  "error": {
    "code": "location_input_unavailable",
    "message": "The location input was not materialized for this result.",
    "result_ref": "12345678901234567890123456789012",
    "input_name": "streets"
  }
}
```

Branch on the code and identifiers, independently of message wording. Other input lookup errors include `output_name=null`, with additional fields for these failures:

- `invalid_pagination`: `parameter` identifies the invalid limit or cursor
- `row_too_large`: `download_url` identifies the complete authenticated Arrow stream
