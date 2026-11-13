---
title: Native location tables
description: Use native provider records and correspondence inputs with separate execution metadata.
contentType: Reference
contentPlan: reference/locations
---

Providers and location handlers use ordinary `geopandas.GeoDataFrame` values. Provider records contain `location_id` and `geometry`; operation-derived inputs also contain `source_location_id`. Read columns, geometry, and CRS directly from the frame.

Declare named location inputs with `LocationDependency`. Each handler receives independent working frames. Provider scopes and original selections remain in execution-owned metadata. Local outputs are available through `result.tables[name]`; their descriptions are in `result.metadata.outputs[name]`.
