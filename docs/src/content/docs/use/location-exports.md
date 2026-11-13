---
title: Working with GIS files
description: Read and write standalone GIS files with native GeoPandas methods.
contentType: How-to
contentPlan: use/location-exports
---

Native GeoDataFrames use GeoPandas file methods. Choose the file format according to the columns and geometry supported by the receiving tool.

```python
import geopandas as gpd

places = gpd.read_file("places.gpkg")
places.to_file("shared-places.gpkg", driver="GPKG")
```

Read native table files with the [ordinary library readers](../../reference/location-artifacts/). Provider scope and execution metadata are supplied separately by managed [result access](../results/).
