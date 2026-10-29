---
title: Supply spatial inputs
description: Supply named ID selections and preserve their coordinate systems.
contentType: How-to
contentPlan: use/spatial-inputs
sidebar: { label: "Supply Spatial Inputs" }
---

A metric can accept multiple named location inputs. The HTTP API, Python clients, Model Context Protocol (MCP), and local execution use the same ID selection for each input:

```json
{"collection": "example:areas", "unit_type": "area", "ids": ["area-1"]}
```

Use these endpoints to construct your inputs:

- `GET /metrics`: read `location_collections` for available collections, unit types, and coordinate reference systems (CRS)
- `GET /metrics/{name}`: read `request_schema` for input names and descriptions of expected units, geometry kinds, and properties
- `GET /data-types`: read `location_selection_schema` for the schema shared by all named location inputs

Supply a nonempty ID list for every declared location input. Treat IDs as strings whose meaning depends on the collection and unit type. Lyra removes duplicates and preserves the order of their first occurrence.

Supply every related unit your calculation needs, such as all lots belonging to selected blocks. Your application owns completeness checks; Lyra does not discover related IDs. See [Aggregate selected lots by block](../../plugins/location-example/) for a runnable example.

The worker resolves exactly those IDs through the configured provider during execution. Unknown IDs and invalid scopes fail the job with input and node context. Submission rejects malformed selections and caller-supplied geometry.

Workers fail the job if the provider cannot acquire locations or returns invalid values. They keep provider resources available to handlers and close them when execution finishes. Python clients submit ID selections to the server.

Providers declare the CRS of resolved geometries. Calculations perform any necessary reprojection. See [results](../results/#understand-spatial-results) for spatial output encoding and [local execution](../local-execution/#supply-id-selections) for provider overrides.
