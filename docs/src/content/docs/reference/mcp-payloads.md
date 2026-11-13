---
title: Inspect MCP request and response payloads
description: Inspect complete representative tool requests and serialized job responses.
contentType: Reference
contentPlan: reference/mcp-payloads
sidebar: { label: "MCP Payload Examples" }
---

These Model Context Protocol (MCP) examples show JSON tool requests and responses. Response examples show `structuredContent`, with inspection payloads under `structuredContent.result`. The text content represents the same data.

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
