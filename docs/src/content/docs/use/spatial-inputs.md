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

The configured provider resolves exactly those IDs before Lyra accepts the job. Lyra closes provider resources before enqueueing; workers reuse the resolved features. If an ID is unknown or belongs to another unit type, Lyra rejects the entire submission and reports the input and IDs. Lyra also rejects caller-supplied geometry.

If the provider cannot fetch locations, submission returns `location_provider_unavailable` with retry guidance. Lyra queues no job when resolution fails. Python clients send your selections to the server for resolution.

Providers declare the CRS of resolved geometries. Calculations perform any necessary reprojection. See [results](../results/#understand-spatial-results) for spatial output encoding and [local execution](../local-execution/#supply-id-selections) for provider overrides.
