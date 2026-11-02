---
title: Use the HTTP API
description: Discover a metric contract, submit a job, and retrieve results over HTTP.
contentType: How-to
contentPlan: use/rest-api
sidebar: { label: "Use The HTTP API" }
---

Use the Hypertext Transfer Protocol (HTTP) application programming interface (API) to run a metric and retrieve its results:

1. Discover a metric and its input requirements.
2. Submit a job.
3. Check job status.
4. Download the result.

For a complete first run with curl, start with the [server tutorial](../../quickstart/).

The [HTTP reference](../../reference/generated/http/) lists routes and authentication requirements. The downloadable OpenAPI contract contains request and response schemas.

## Authenticate requests

Use the credential assigned to the endpoint family you’re calling:

| Endpoint family | Credential |
| --- | --- |
| Health, metric catalog, ID-selection schemas | Public |
| Every `/jobs` route | Agent key |
| Model Context Protocol (MCP) transport | Agent key |
| Every `/admin` route | Separate administrator key |

For protected requests, send:

```text
Authorization: Bearer your_agent_key_here
```

Keep the administrator key with operators. Missing or malformed agent authentication returns `401`; an incorrect token returns `403`.

## Read the live contract

Call `GET /metrics` to list metric families: metrics that share an input and output contract across editions. Then call `GET /metrics/{name}` to inspect a family’s contract.

Each summary lists available editions and describes what each output row represents. Control the listing with these query parameters:

- `query`: search terms
- `offset`: entries to skip
- `limit`: page size, at most 100
 The `ETag` response header identifies the public contract, so you can detect changes.

Read these fields in the inspection response:

- `parameters`: ordinary parameter constraints and defaults
- `native_inputs`: the location types the metric calculates on
- `outputs`: output schemas, units, and definitions

Add `?edition=your_edition_here` to inspect a named edition’s location contracts (`bindings`) and supported source types (`accepted_selections`). Include that edition in the submission’s top-level `edition` field, even if the family has only one named edition. For metrics without named editions, omit `edition`; inspection returns their executable contract directly.

## Construct the input

Ordinary inputs go inside `input.parameters`. Named ID selections, such as `location`, are siblings of `parameters`:

```json
{
  "metric": "smoke_table_metric",
  "input": {
    "location": {
      "collection": "test:units",
      "unit_type": "lot",
      "ids": ["area-1"]
    },
    "parameters": {
      "value": 7
    }
  }
}
```

Include any required parameters. You can omit `parameters` when an empty object satisfies the metric’s parameter schema. For example, a metric with no parameter model needs only its location selection:

```json
{
  "metric": "smoke_summary_metric",
  "input": {
    "location": {
      "collection": "test:units",
      "unit_type": "lot",
      "ids": ["area-1"]
    }
  }
}
```

For this smoke metric, keep `test:units` and `lot` and supply IDs known to its provider. For another metric, use its inspected accepted selections. See [spatial inputs](../spatial-inputs/) for selection rules and validation errors.

## Submit and observe

Send your input to `POST /jobs`. Lyra validates the request and records its selected edition, routes, and data revisions. It then returns `202` with a job ID. Poll `GET /jobs/{job_id}` every five seconds until the status is `succeeded` or `failed`.

Each POST creates a separate job. A network timeout can occur after acceptance; a repeated POST can duplicate the calculation. Lyra does not automatically retry or deduplicate submissions.

The API rejects input schema violations with `422`. Python validation in a worker can still reject an accepted request. The [jobs guide](../jobs/) explains that distinction and how to respond to failures.

## Retrieve a completed result

During execution, Lyra resolves your IDs to location data using the revisions recorded at submission. Unknown IDs or unavailable revisions cause the job to fail. Inspecting a failed job still returns HTTP `200`; check the response’s `status` field for the execution outcome.

Choose a route based on what you need:

| Route | Use |
| --- | --- |
| `GET /jobs/{job_id}/result/preview` | Read at most five rows total across outputs; `X-Result-Truncated: true` indicates omitted rows |
| `GET /jobs/{job_id}/result/descriptor` | Inspect outputs, provenance, and remaining lifetime |
| `GET /jobs/{job_id}/result` | Read the complete terminal JSON result |
| `GET /jobs/{job_id}/result/download` | Download the complete terminal JSON result |

The `/result` and `/result/download` routes return the same complete JSON envelope, including failures. Successful results include every declared output of the requested metric; intermediate dependency tables stay internal. Download what you need before retention ends.

Continue to [Read and download results](../results/) for table encoding, identifiers, and geometry.
