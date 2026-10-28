---
title: MCP payload examples
description: Inspect complete representative tool requests and serialized job responses.
contentType: Reference
contentPlan: reference/mcp-payloads
sidebar: { label: "MCP Payload Examples" }
---

These examples show the JSON available in `structuredContent` and the tool’s text content. Optional null fields can also be present. Authentication instructions name an environment variable rather than including its secret.

For the procedure, read [the MCP workflow](../../use/mcp/). For exact schemas, use [the tool reference](../generated/mcp/).

## Submission input

Supply the metric, metropolitan-zone code, and declared parameters.

```json
{
  "metric": "smoke_table_metric",
  "met_zone_code": "09.01",
  "parameters": {
    "value": 7
  }
}
```

## Submission response

Keep the result reference for subsequent observations.

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "next_tool": "lyra_get_job_result"
}
```

## Queued job

Wait for the advertised polling interval before inspecting again.

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "status": "queued",
  "metric": "smoke_table_metric",
  "created_at": "2026-09-24T12:00:00Z",
  "updated_at": "2026-09-24T12:00:00Z",
  "poll_after_seconds": 2,
  "next_tool": "lyra_get_job_result",
  "truncation": {
    "omitted_rows": 0,
    "omitted_columns": 0,
    "shortened_strings": 0,
    "omitted_sections": []
  }
}
```

## Running job

Progress is optional. Lifecycle status determines completion.

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "status": "running",
  "metric": "smoke_table_metric",
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
  "next_tool": "lyra_get_job_result",
  "truncation": {
    "omitted_rows": 0,
    "omitted_columns": 0,
    "shortened_strings": 0,
    "omitted_sections": []
  }
}
```

## Successful table result

A successful response groups metadata, previews, and truncation information by output name. This excerpt shows the submitted value in the named table:

```json
{"outputs": {"smoke_table_metric": {"preview": [{"value": 7}]}}}
```

Expand the complete response to inspect its remaining fields.

<details>
<summary>Complete response</summary>

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
  "status": "succeeded",
  "result_kind": "table",
  "completed_at": "2026-09-24T12:00:10Z",
  "lifetime": {
    "expires_in_seconds": 3600
  },
  "descriptor": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/job-1/result/descriptor",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  },
  "truncation": {
    "omitted_rows": 0,
    "omitted_columns": 0,
    "shortened_strings": 0,
    "omitted_sections": []
  },
  "outputs": {
    "smoke_table_metric": {
      "metadata": {
        "row_count": 1,
        "declaration": {
          "kind": "table",
          "columns": [],
          "geometry": null
        },
        "spatial": false,
        "column_count": 1,
        "summary": {
          "value": {
            "null_count": 0,
            "non_null_count": 1,
            "minimum": 7,
            "maximum": 7
          }
        }
      },
      "preview": [
        {
          "value": 7
        }
      ],
      "truncation": {
        "omitted_rows": 0,
        "omitted_columns": 0,
        "shortened_strings": 0,
        "omitted_sections": []
      }
    }
  }
}
```

</details>

## Failed job

The tool call succeeds (`isError: false`) even when the job fails, because it successfully reports the terminal outcome.

```json
{
  "job_id": "failed-1",
  "result_ref": "lyra://results/failed-1",
  "status": "failed",
  "result_kind": "failed",
  "lifetime": {},
  "descriptor": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/failed-1/result/descriptor",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  },
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
```

## Unknown or expired reference

A tool error reports `result_not_found` with `isError: true`.

```json
{
  "error": {
    "code": "result_not_found",
    "message": "This result reference is unknown or expired.",
    "details": {
      "job_id": "old-1",
      "result_ref": "lyra://results/old-1"
    }
  }
}
```

## Download handoff

Send the agent Bearer credential when fetching the supplied URL.

```json
{
  "job_id": "job-1",
  "result_ref": "lyra://results/job-1",
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

This separate example uses a metric with an ordinary summary table and a spatial parcel table. Each output has its own metadata and preview, as the selected fields below illustrate:

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

Expand the complete response to inspect its remaining fields.

<details>
<summary>Complete response</summary>

```json
{
  "job_id": "mixed-1",
  "result_ref": "lyra://results/mixed-1",
  "status": "succeeded",
  "result_kind": "table",
  "lifetime": {},
  "descriptor": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/mixed-1/result/descriptor",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  },
  "outputs": {
    "summary": {
      "metadata": {
        "row_count": 1,
        "declaration": {
          "kind": "table",
          "columns": [],
          "geometry": null
        },
        "spatial": false,
        "crs": null,
        "geometry_types": [],
        "column_count": 1,
        "summary": {
          "count": {
            "null_count": 0,
            "non_null_count": 1,
            "minimum": 1,
            "maximum": 1
          }
        }
      },
      "preview": [
        {
          "count": 1
        }
      ],
      "truncation": {
        "omitted_rows": 0,
        "omitted_columns": 0,
        "shortened_strings": 0,
        "omitted_sections": []
      }
    },
    "parcels": {
      "metadata": {
        "row_count": 1,
        "declaration": {
          "kind": "table",
          "columns": [],
          "geometry": "geometry"
        },
        "spatial": true,
        "crs": "EPSG:4326",
        "geometry_types": [
          "Point"
        ],
        "column_count": 1,
        "summary": {
          "count": {
            "null_count": 0,
            "non_null_count": 1,
            "minimum": 2,
            "maximum": 2
          }
        }
      },
      "preview": {
        "type": "FeatureCollection",
        "features": [
          {
            "type": "Feature",
            "geometry": {
              "type": "Point",
              "coordinates": [
                -99,
                20
              ]
            },
            "properties": {
              "count": 2
            }
          }
        ]
      },
      "truncation": {
        "omitted_rows": 0,
        "omitted_columns": 0,
        "shortened_strings": 0,
        "omitted_sections": []
      }
    }
  }
}
```

</details>

## Compact provenance

This response extends the multiple-output example with request provenance. Inspection records the collection size and coordinate reference system (CRS) without coordinates; fetch the descriptor when you need complete provenance:

```json
{
  "provenance": {
    "input": {
      "location": {
        "type": "FeatureCollection",
        "feature_count": 1,
        "crs": "EPSG:4326"
      }
    }
  }
}
```

Expand the complete response to inspect its remaining fields.

<details>
<summary>Complete response</summary>

```json
{
  "job_id": "mixed-1",
  "result_ref": "lyra://results/mixed-1",
  "status": "succeeded",
  "result_kind": "table",
  "lifetime": {},
  "descriptor": {
    "method": "GET",
    "url": "https://lyra.example/api/jobs/mixed-1/result/descriptor",
    "authentication": {
      "scheme": "Bearer",
      "credential_env_var": "LYRA_AGENT_API_KEY"
    }
  },
  "outputs": {
    "summary": {
      "metadata": {
        "row_count": 1,
        "declaration": {
          "kind": "table",
          "columns": [],
          "geometry": null
        },
        "spatial": false,
        "crs": null,
        "geometry_types": [],
        "column_count": 1,
        "summary": {
          "count": {
            "null_count": 0,
            "non_null_count": 1,
            "minimum": 1,
            "maximum": 1
          }
        }
      },
      "preview": [
        {
          "count": 1
        }
      ],
      "truncation": {
        "omitted_rows": 0,
        "omitted_columns": 0,
        "shortened_strings": 0,
        "omitted_sections": []
      }
    },
    "parcels": {
      "metadata": {
        "row_count": 1,
        "declaration": {
          "kind": "table",
          "columns": [],
          "geometry": "geometry"
        },
        "spatial": true,
        "crs": "EPSG:4326",
        "geometry_types": [
          "Point"
        ],
        "column_count": 1,
        "summary": {
          "count": {
            "null_count": 0,
            "non_null_count": 1,
            "minimum": 2,
            "maximum": 2
          }
        }
      },
      "preview": {
        "type": "FeatureCollection",
        "features": [
          {
            "type": "Feature",
            "geometry": {
              "type": "Point",
              "coordinates": [
                -99,
                20
              ]
            },
            "properties": {
              "count": 2
            }
          }
        ]
      },
      "truncation": {
        "omitted_rows": 0,
        "omitted_columns": 0,
        "shortened_strings": 0,
        "omitted_sections": []
      }
    }
  },
  "provenance": {
    "metric": "built_form",
    "catalog_fingerprint": "catalog-1",
    "plugin": {
      "name": "city",
      "version": "1"
    },
    "created_at": "2026-10-27T00:00:00Z",
    "input": {
      "location": {
        "type": "FeatureCollection",
        "feature_count": 1,
        "crs": "EPSG:4326"
      }
    }
  }
}
```

</details>
