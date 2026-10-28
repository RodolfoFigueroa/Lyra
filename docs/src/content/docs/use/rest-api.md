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
| Health, metric catalog, spatial data types, and metropolitan-zone lookup | Public |
| Every `/jobs` route | Agent key |
| MCP transport | Agent key |
| Every `/admin` route | Separate administrator key |

For protected requests, send:

```text
Authorization: Bearer your_agent_key_here
```

Keep the administrator key with operators. Missing or malformed agent authentication returns `401`; an incorrect token returns `403`.

## Read the live contract

Call `GET /metrics` to list metrics, then `GET /metrics/{name}` for the one you want. Its `request_schema` defines the public input. Its `spatial_inputs` and `outputs` explain region arguments and named result tables.

A catalog fingerprint identifies the published catalog contract. Use the deployed schema when constructing a request; plugin versions can differ between servers.

## Construct the input

Ordinary inputs go inside `input.parameters`. Spatial selectors, such as `location`, are siblings of `parameters`:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {
      "data_type": "met_zone_code",
      "value": "09.01"
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
      "data_type": "met_zone_code",
      "value": "09.01"
    }
  }
}
```

See [spatial inputs](../spatial-inputs/) for geographic codes, explicit GeoJSON, and the distinction between `location` and `bounds`.

## Submit and observe

`POST /jobs` returns `202` with a job ID when submission succeeds. `GET /jobs/{job_id}` returns the latest status. Check it roughly every five seconds until it reaches `succeeded` or `failed`.

Each POST creates a separate job. A network timeout can occur after acceptance; a repeated POST can duplicate the calculation. Submission is not automatically retried or deduplicated.

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
