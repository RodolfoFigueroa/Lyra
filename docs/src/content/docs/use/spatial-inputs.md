---
title: Supply spatial inputs
description: Choose a geographic selector or explicit GeoJSON and preserve its coordinate system.
contentType: How-to
contentPlan: use/spatial-inputs
sidebar: { label: "Supply Spatial Inputs" }
---

A metric can accept `location`, `bounds`, both, or neither. Read its live `spatial_inputs` and `request_schema` before constructing the input.

| Argument | Resolved value |
| --- | --- |
| `location` | A FeatureCollection containing one or more Polygon or MultiPolygon features |
| `bounds` | A FeatureCollection containing exactly one Polygon feature |

A bounds polygon does not have to be rectangular. The metric’s own documentation should explain which regions and scales are meaningful for its calculation.

## Choose a server-side selector

Call `GET /data-types` to inspect supported selectors. Common examples are a metropolitan-zone code and a list of census geographic identifiers:

```json
{
  "data_type": "met_zone_code",
  "value": "09.01"
}
```

```json
{
  "data_type": "cvegeo_list",
  "value": ["090020001"]
}
```

Put the selector under the metric’s spatial argument, such as `input.location`. Lyra looks up the geometry in PostGIS before queuing the calculation.

These selectors require [PostgreSQL configuration](../../operate/configuration/#configure-postgresql-access) and the matching spatial data. Without `[database]`, submission returns `database_not_configured` with `retryable=false`, and Lyra queues no job.

## Send your own geometry

Use the `geojson` selector to supply a polygon collection explicitly:

```json
{
  "data_type": "geojson",
  "value": {
    "type": "FeatureCollection",
    "features": [{
      "type": "Feature",
      "id": "area",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [[-99.2, 19.3], [-99.1, 19.3], [-99.1, 19.4], [-99.2, 19.3]]
        ]
      },
      "properties": {}
    }],
    "crs": {
      "type": "name", "properties": {"name": "EPSG:4326"}
    }
  }
}
```

Lyra validates explicit GeoJSON without querying PostgreSQL. You can submit these inputs to a deployment without `[database]`. The metric or its dependencies still need PostgreSQL if they request the `database` resource.

## Keep track of coordinate systems

Supply the coordinate reference system (CRS) that describes the coordinates you actually send. Lyra preserves the declared input CRS through resolution and native table conversion. Calculations perform any reprojection they require.

Server result encoding is a separate step: spatial output tables become GeoJSON using World Geodetic System 1984 (WGS84). Local runner results keep the native GeoDataFrame CRS. See [results](../results/#understand-spatial-results).

## Use the right form for your interface

The HTTP API and Python client support both spatial arguments together when a metric declares them. Their inputs use selectors as shown above.

`LocalRunner` accepts the resolved FeatureCollection value directly. It does not unwrap selectors or look up geographic codes.

For Model Context Protocol (MCP) submissions, call `lyra_run_metric` with the same `metric` and `input` fields as HTTP. Put each spatial selector inside `input`, as shown in the [MCP submission example](../mcp/#2-submit-once).
