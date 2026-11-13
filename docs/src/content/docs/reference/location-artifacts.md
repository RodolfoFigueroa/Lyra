---
title: Reading native tables
description: Read native tables and retrieve managed result descriptions separately.
contentType: Reference
contentPlan: reference/location-artifacts
---

Use ordinary pandas and GeoPandas readers for standalone files:

```python
import pandas as pd
import geopandas as gpd

rows = pd.read_parquet("rows.parquet")
places = gpd.read_parquet("places.parquet")
```

For retained jobs, use the [result interfaces](../../use/results/). Managed retrieval returns native tables and keeps output descriptions separate from frames.
