---
title: Read native table files
description: Read standalone tables and inspect metadata from managed GeoParquet exports.
contentType: Reference
contentPlan: reference/location-artifacts
---

Use pandas and GeoPandas to read standalone table files. Managed GeoParquet exports also include a Lyra description and references to retained results and reports. These references require the associated store or deployment.

## Native readers

Use pandas for nonspatial Parquet tables and GeoPandas for spatial tables:

```python
import pandas as pd
import geopandas as gpd

rows = pd.read_parquet("rows.parquet")
places = gpd.read_parquet("places.parquet")
```

For retained jobs, use the [result interfaces](../../use/results/). Managed retrieval returns native tables and keeps output descriptions separate from frames.

## GeoParquet metadata

After you read a [managed GeoParquet export](../../use/location-exports/), `frame.attrs["lyra"]` contains a JSON-compatible dictionary. This dictionary describes the selected table:

| Field | Meaning |
| --- | --- |
| `kind` | `metric_output` or `location_input` |
| `result_ref` | Originating retained result reference |
| `execution_id` | Execution associated with that result, including cache reuse |
| `output_name` or `input_name` | Selected name, according to `kind` |
| `description` | Complete selected description serialized with `model_dump(mode="json")`, including null fields |

### Selected descriptions

The description records the table’s schema and applicable [provider scopes](../locations/). Null scope fields retain their meaning:

| Selection | Description fields and scope meanings |
| --- | --- |
| Metric output | `scope`, `table_schema`, and `owner_scope`; unpartitioned outputs have `owner_scope=None` |
| Direct provider input | `kind="provider"`, `table_schema`, `provider_scope`, `source_scope=None`, and `availability="available"` |
| Correspondence input | `kind="correspondence"`, `table_schema`, `provider_scope`, `source_scope`, and `availability="available"` |

For correspondence inputs, `source_scope` identifies the immediate source and `provider_scope` identifies the target. These roles describe the final operation, even when execution chains several operations. An unpartitioned output’s inputs don’t assign it an owner scope.

### Record and execution identity

Each scope retains the provider and origin identities from the retained execution:

- Provider ID and code revision
- Deduplicated origin IDs and revisions
- Applicable provider and origin descriptions

An omitted origin revision identifies fixed unversioned data. A provider scope and `location_id` together identify a provider record; the bare ID doesn’t identify it across datasets.

Computed outputs keep their own result and named-table identity. Structural columns identify records within their applicable scopes:

| Table | Structural columns |
| --- | --- |
| Direct provider input | `location_id` |
| Location-scoped metric output | `location_id` and `row_key`, unique within the owner and named output |
| Correspondence input | Immediate `source_location_id` and target `location_id` |
| Unpartitioned output | Only declared columns; export doesn’t add ownership IDs or row keys |

Correspondence geometry and attributes belong to the target, which may appear under several sources. Result and execution references identify retained results and runs separately from provider records. Runs that reuse values can have different result and execution references.

### Schema and preservation

Read structural IDs as ordinary columns and CRS through `frame.crs`. The active geometry column is `geometry`. Standard GeoParquet metadata and the logical schema both record the CRS.

The selected `table_schema` records these declarations:

- Column types and nullability
- Column descriptions and units
- Geometry name and CRS

Empty tables retain these declarations without fabricated rows. Lyra writes only declared columns with `index=False`. Export doesn’t guarantee preservation of pandas indexes, row order, or arbitrary dtype widths and backing arrays.

### Retained references

Supply the associated store or deployment to [follow references to descriptions and reports](../../use/location-exports/#follow-references-to-reports). Reports stay in the retained store; the file includes neither report bodies nor the store’s location. File metadata doesn’t register a provider or bind a managed execution.

Third-party rewriting may discard custom attributes. Files without Lyra metadata remain readable spatial files, and file checksums don’t establish provider record identity.
