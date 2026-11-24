---
title: Use the HTTP API
description: Discover a metric contract, submit a job, and retrieve results over HTTP.
contentType: How-to
contentPlan: use/rest-api
sidebar: { label: "Use The HTTP API" }
---

Use the HTTP API to run metrics on a Lyra server. Keep the accepted job ID so you can check status and retrieve the result. Follow this workflow:

1. Discover a metric and its input requirements.
2. Submit a job.
3. Check job status.
4. Download the result.

For a guided first run with curl, start with the [server tutorial](../../quickstart/).

The [HTTP reference](../../reference/generated/http/) lists routes and authentication requirements. The downloadable OpenAPI contract contains request and response schemas.

## Authenticate requests

Use the credential for the endpoint family you’re calling:

| Endpoint family | Credential |
| --- | --- |
| Health, metric catalog, ID-selection schemas | Public |
| Every `/jobs` route | Agent key |
| Every `/results` and `/executions` route | Agent key |
| Model Context Protocol (MCP) transport | Agent key |
| Every `/admin` route | Separate administrator key |

For protected requests, send:

```text
Authorization: Bearer your_agent_key_here
```

Keep the administrator key with operators. Missing or malformed agent authentication returns `401`; an incorrect token returns `403`.

## Read the live contract

Call `GET /metrics` to list registered public metrics. Then call `GET /metrics/{name}` to inspect the selected metric’s contract.

Each summary lists accepted selections and describes what each output row represents. Control the listing with these query parameters:

- `query`: search terms
- `offset`: number of matching metrics to skip, starting at zero
- `limit`: page size, from 1 to 100

If the offset reaches or exceeds the match count, the API returns `200` with `[]`. Every page includes the catalog fingerprint in its `ETag` header. To list one catalog version, compare `ETag` across pages and restart at zero if it changes.

Use the inspection response from `GET /metrics/{name}` to prepare your request.

| Field | What it tells you |
| --- | --- |
| `metric.accepted_selections` | Which source provider IDs you can select for each input you supply |
| `location_inputs` | Which provider each input targets. For a derived input, `operation` names the operation and `source_provider` identifies its immediate source. |
| `providers` | Each provider’s record schema, coordinate reference system, and accepted sources. `query_capable` and `membership_capable` indicate support for queries and membership checks. |
| `parameters` | Which parameter values the metric accepts and which defaults it uses |
| `computed_inputs` | Which inputs Lyra supplies through dependencies |
| `outputs` | Result tables, column definitions, and units |

Submit the registered name in `metric`. Supply the inputs listed in `metric.accepted_selections` and choose settings from `parameters`.

## Construct the request

Set `metric` to the registered name. Use the following fields for the selections, settings, and changes your run needs.

- `parameters`: calculation values, such as `condition_threshold`
- `inputs`: named ID selections, such as `streets`
- `interventions`: an array of [inline additions and removals](../../reference/mcp-payloads/#submit-an-inline-street-proposal), with one set per target catalog

This request selects one street and sets its condition threshold:

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

Include any required parameters. You can omit `parameters` when an empty object satisfies the metric’s parameter schema. For example, a metric with no parameter model needs only its location selection:

```json
{
  "metric": "city_street_lengths",
  "inputs": {
    "streets": {
      "provider": "city_streets",
      "ids": ["street-1"]
    }
  }
}
```

For either city metric, select the `city_streets` provider and supply IDs from its dataset. The maintenance request returns `maintenance`; the lengths request returns `lengths`. Both return one row for `street-1` with `length_m` equal to 100. The maintenance row also reports `maintenance_length_m` equal to 100.

For another metric, use its inspected accepted selections. See [spatial inputs](../spatial-inputs/) for selection rules and validation errors.

## Submit and observe

Send your input to `POST /jobs`. Lyra validates the request and records its selected metric, routes, and data revisions. It then returns `202` with a job ID. Poll `GET /jobs/{job_id}` every five seconds until the status is `succeeded` or `failed`.

Each POST creates a separate job. A network timeout can occur after acceptance; a repeated POST can duplicate the calculation. Lyra does not automatically retry or deduplicate submissions.

The API rejects input schema violations with `422`. Python validation in a worker can still reject an accepted request. The [jobs guide](../jobs/) explains that distinction and how to respond to failures.

## Retrieve a completed result

During execution, Lyra resolves your IDs to location data using the revisions recorded at submission. Unknown IDs or unavailable revisions cause the job to fail. Inspecting a failed job still returns HTTP `200`; check the response’s `status` field for the execution outcome.

Pass the 32 lowercase hexadecimal characters you received at submission. On a matching route, an authorized request with a malformed ID returns HTTP 422. The validation error identifies `["path", "job_id"]`. A valid ID with no matching resource returns HTTP 404.

All job lookup endpoints return `404` when the job is missing or expired. Lyra returns `500` when data required by a lookup is missing or invalid. The error includes `detail.code: invalid_job_result` and `detail.retryable: false`, without exposing stored data. You can still check status and administrator details when successful result files are unavailable.

Result endpoints return `202` while the job is queued or running. Choose a route based on what you need:

| Route | Use |
| --- | --- |
| `GET /jobs/{job_id}/result/inspection` | Inspect bounded summaries, previews, compact provenance, and remaining lifetime |
| `GET /jobs/{job_id}/result` | Read the complete terminal JSON result |
| `GET /jobs/{job_id}/result/download` | Download the complete terminal JSON result |

Both `/result` and `/result/download` return the complete job result as JSON. A successful job includes every named output of the requested metric. A failed job includes failure details. Download the job result before its queue record expires.

To read the complete report, send an authenticated `GET /executions/{job_id}/report/download` request. This route accepts the submission’s job ID even after queue expiry.

## Download a retained table

Download one saved output using its `job_id` and `output_name`, even after the job expires. Choose the output name from the [result description](../results/#read-a-retained-description).

Send `GET /results/{job_id}/tables/{output_name}/download` with your agent Bearer credential, percent-encoding each path segment. Lyra returns `200` with content type `application/vnd.apache.arrow.stream` when the lookup succeeds.

Save the response bytes unchanged as `output.arrow`. The download uses Arrow’s interprocess communication (IPC) stream format regardless of the filename. Open the saved stream with PyArrow:

```python {3-4}
import pyarrow as pa

with open("output.arrow", "rb") as source, pa.ipc.open_stream(source) as reader:
    table = reader.read_all()
```

For a nonspatial output, reconstruct the DataFrame:

```python
frame = table.to_pandas()
```

For spatial outputs, GeoArrow stores geometry as well-known binary (WKB) with its declared coordinate reference system (CRS). Use GeoPandas to reconstruct the GeoDataFrame without changing coordinates:

```python
import geopandas as gpd

points = gpd.GeoDataFrame.from_arrow(table)
```

Arrow preserves declared types and values, including:

- Exact string IDs and signed 64-bit integers
- Duplicate rows and null values
- Native geometry coordinates, with missing and empty geometries kept distinct

Empty outputs retain their complete schema. Read result identity and provider ownership from the separate result description.

Lyra generates the download from saved rows without rerunning the metric. If the lookup fails, inspect `detail.code` in the response:

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `404` | `result_not_found` | The result or output name doesn’t exist |
| `500` | `result_integrity_error` | Required stored data is missing or damaged |

Each lookup error includes `job_id` and the requested `output_name`. If the download stops midstream, discard the partial file.

Continue to [Read and download results](../results/) for table encoding, identifiers, and geometry.

## Read retained location inputs

Use the job ID to read the location inputs saved for the requested metric. Choose an `input_name` from `location_inputs` in the [result description](../results/#read-a-retained-description).

Inspect each input’s schema and `availability` before reading its rows. Its [provider scope](../../concepts/caching/#reuse-tables-across-executions) identifies the dataset and revisions. Correspondences map immediate source records to target records, so their descriptions also identify the source scope.

Descriptions include empty and `not_materialized` inputs without loading their rows. If the description exceeds the inspection limit, Lyra omits it entirely and returns `omitted=["description"]`. Follow the preview’s authenticated `download_url` to retrieve every output and input description.

Send your agent Bearer credential to either GET endpoint, percent-encoding each path segment:

| Read | Endpoint |
| --- | --- |
| JSON page | `/results/{job_id}/location-inputs/{input_name}/rows` |
| Arrow stream | `/results/{job_id}/location-inputs/{input_name}/download` |

Successful requests return `200`. For pages, set a positive integer `limit` to change the default of 100 rows. Pass `next_cursor` unchanged as `cursor` with the same result and input until it returns `null`. Continue after short pages when a cursor remains; output-table cursors aren’t valid for input rows.

Lyra returns complete rows within the response size limit. Interpret cells using these encodings:

- **Integers**: decimal strings
- **Geometry**: well-known text (WKT) in the saved CRS
- **Missing cells**: `null`

Downloads use `application/vnd.apache.arrow.stream`, preserving the declared schema and CRS even for empty selections. Open the stream with [PyArrow or GeoPandas](#download-a-retained-table). You can retrieve these inputs after job expiry or plugin registration changes while the retained store remains available.

Direct inputs contain unique provider records. Correspondences contain each immediate source/target pair, including repeated targets under different sources. Input rows have no metric `row_key`.

If a request fails, inspect `detail.code`:

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `result_not_found` | Missing result or undeclared location input |
| `409` | `location_input_unavailable` | Lyra didn’t materialize this input for the result |
| `500` | `result_integrity_error` | Required retained data or relationships are missing or damaged |
| `422` | `invalid_pagination` | Invalid limit or cursor; see `parameter` |
| `422` | `row_too_large` | Next row cannot fit; use the Arrow `download_url` |

Each lookup error includes `job_id` and `input_name`. All except `location_input_unavailable` also include `output_name=null`. Retrying an unavailable input leaves its saved selection unchanged.

For `row_too_large`, fetch `download_url` with your agent Bearer credential to retrieve the complete Arrow stream. The failed page request leaves the cursor at the oversized row. If generation fails after streaming starts, discard the partial download.
