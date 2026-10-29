---
title: Use the HTTP API
description: Discover a metric contract, submit a job, and retrieve results over HTTP.
contentType: How-to
contentPlan: use/rest-api
sidebar: { label: "Use The HTTP API" }
---

The HTTP workflow has four steps: discover a metric, submit its input, check job status, and download the result. If you want a complete first run with curl, start with the [server tutorial](../../quickstart/).

The [HTTP reference](../../reference/generated/http/) lists routes and authentication requirements. The downloadable OpenAPI contract contains request and response schemas.

## Authenticate requests

Use the credential assigned to the endpoint family you’re calling:

| Endpoint family | Credential |
| --- | --- |
| Health, metric catalog, ID-selection schemas | Public |
| Every `/jobs` route | Agent key |
| MCP transport | Agent key |
| Every `/admin` route | Separate administrator key |

For protected requests, send:

```text
Authorization: Bearer your_agent_key_here
```

Keep the administrator key with operators. Missing or malformed agent authentication returns `401`; an incorrect token returns `403`.

## Read the live contract

Call `GET /metrics` to list metrics, then `GET /metrics/{name}` to inspect the one you want:

- `request_schema` defines the input fields and their descriptions
- `spatial_inputs` identifies named location arguments
- `outputs` describes the result tables

The catalog’s `location_collections` lists the provider’s collections, unit types, and coordinate reference systems (CRS). Reading this metadata does not access the provider’s data source.

A catalog fingerprint identifies the published catalog contract. Use the deployed schema when constructing a request; plugin versions can differ between servers.

## Construct the input

Ordinary inputs go inside `input.parameters`. Named ID selections, such as `location`, are siblings of `parameters`:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {
      "collection": "example:areas",
      "unit_type": "area",
      "ids": ["area-1"]
    },
    "parameters": {
      "value": 7
    }
  }
}
```

If a metric declares a parameter model, include the object even when it is empty. For a metric with no parameter model, omit it:

```json
{
  "metric": "smoke_summary_metric",
  "input": {
    "location": {
      "collection": "example:areas",
      "unit_type": "area",
      "ids": ["area-1"]
    }
  }
}
```

Replace the example collection, unit type, and IDs with values supported by your deployment. See [spatial inputs](../spatial-inputs/) for selection rules and validation errors.

## Submit and observe

`POST /jobs` resolves your ID selections before returning `202` with a job ID. Use `GET /jobs/{job_id}` to check status every five seconds until the job reaches `succeeded` or `failed`.

Each POST creates a separate job. A network timeout can occur after acceptance; a repeated POST can duplicate the calculation. Lyra does not automatically retry or deduplicate submissions.

The API rejects input schema violations with `422`. Python validation in a worker can still reject an accepted request. The [jobs guide](../jobs/) explains that distinction and how to respond to failures.

## Retrieve a completed result

Choose the descriptor to inspect metadata, or a result route to retrieve the complete retained payload:

| Route | Use |
| --- | --- |
| `GET /jobs/{job_id}/result/descriptor` | Inspect outputs, provenance, and remaining lifetime |
| `GET /jobs/{job_id}/result` | Read the complete terminal JSON result |
| `GET /jobs/{job_id}/result/download` | Download the successful JSON result |

Successful result reads and downloads contain the same complete JSON envelope. The requested metric publishes all its declared outputs; intermediate dependency tables stay internal. Download what you need before retention ends.

Continue to [Read and download results](../results/) for table encoding, identifiers, and geometry.
